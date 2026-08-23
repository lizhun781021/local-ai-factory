#!/usr/bin/env python3
"""
模型生命周期引擎 (LifecycleManager)
====================================
统一管理所有本地模型的按需启动与空闲卸载，取代 ai-idle-watchdog。

职责：
  1. 按需预热：路由到离线模型且 auto_start=true → 后台异步拉起，本次走降级链立即返回
  2. 空闲卸载：本地模型空闲超过 idle_timeout → 自动卸载释放内存
  3. 活跃追踪：转发成功时记录最后活跃时间（比解析日志更准）
  4. 状态查询：提供每个模型的生命周期状态供看板展示

设计要点：
  - 后台守护线程做空闲扫描，不阻塞转发请求
  - ensure_started() 异步拉起，调用方立即返回不等待
  - 拉起后进入 startup_grace 宽限期，期间不判空闲（等模型装载完毕）
  - 远程模型不占本地资源，不纳入管理
"""

import time
import threading
import subprocess
import logging
from datetime import datetime
from typing import Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

import httpx

logger = logging.getLogger("lifecycle")


class LifecycleManager:
    """模型生命周期管理器"""

    def __init__(self, engine):
        """
        Args:
            engine: RouterEngine 实例（用于健康检查和获取模型配置）
        """
        self.engine = engine
        # 生命周期配置
        self.enabled = True
        self.idle_check_interval = 60
        self.default_idle_timeout = 600
        self.startup_check_interval = 10
        self.startup_timeout = 300
        self.startup_grace = 300
        self._load_config()

        # 运行时状态（线程安全）
        self._lock = threading.Lock()
        self.last_active: dict[str, float] = {}       # 模型 → 最后活跃时间戳
        self.started_at: dict[str, float] = {}         # 模型 → 最近拉起时间戳
        self.starting: set[str] = set()                # 正在拉起中的模型
        self.last_unload: dict[str, float] = {}        # 模型 → 最近卸载时间戳（避免卸载后立刻又拉）
        self._health_cache: dict[str, tuple] = {}      # 模型 → (时间戳, healthy) 健康检查缓存

        # 守护线程
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def _load_config(self):
        """从 engine 配置加载生命周期参数"""
        lc = self.engine.lifecycle_config or {}
        self.enabled = lc.get('enabled', True)
        self.idle_check_interval = lc.get('idle_check_interval', 60)
        self.default_idle_timeout = lc.get('default_idle_timeout', 600)
        self.startup_check_interval = lc.get('startup_check_interval', 10)
        self.startup_timeout = lc.get('startup_timeout', 300)
        self.startup_grace = lc.get('startup_grace', 300)

    # -------------------- 受管模型判定 --------------------
    def _is_managed(self, model_name: str) -> bool:
        """是否纳入生命周期管理（本地模型 + 有 idle_timeout 配置）"""
        m = self.engine.models.get(model_name)
        if not m or not m.enabled:
            return False
        # 远程模型（cost=free 且无本地命令）不管
        if m.cost == 'free' and not m.startup_cmd and not m.shutdown_cmd:
            return False
        # 有 idle_timeout 配置（>0）才管；0 表示常驻不卸载
        timeout = self._get_idle_timeout(model_name)
        return timeout > 0

    def _get_idle_timeout(self, model_name: str) -> int:
        """获取模型空闲超时（0=常驻）"""
        m = self.engine.models.get(model_name)
        if not m:
            return 0
        if hasattr(m, 'idle_timeout') and m.idle_timeout is not None:
            return m.idle_timeout
        # 本地模型但未配置 → 用默认值
        if m.cost == 'local':
            return self.default_idle_timeout
        return 0

    def _has_lifecycle(self, model_name: str) -> bool:
        """模型是否有生命周期命令（用于状态展示）"""
        m = self.engine.models.get(model_name)
        if not m:
            return False
        return bool(m.startup_cmd or m.shutdown_cmd) or m.cost == 'local'

    def _is_model_loaded(self, model_name: str) -> bool:
        """
        检查模型是否已加载到内存（比健康检查更细粒度）
        - ollama: 查 /api/ps 看具体模型是否在运行列表（服务在线≠模型加载）
        - openai(MLX): 端口健康检查
        """
        m = self.engine.models.get(model_name)
        if not m:
            return False
        if m.api_type == 'ollama':
            try:
                resp = httpx.get(f"{m.base_url}/api/ps", timeout=5)
                if resp.status_code == 200:
                    loaded = [x.get('name', '') for x in resp.json().get('models', [])]
                    return m.model_name in loaded
            except Exception:
                pass
            return False
        else:
            return self.engine.check_model_health(model_name)

    # -------------------- 活跃时间追踪 --------------------
    def on_request_success(self, model_name: str):
        """转发成功后调用，更新最后活跃时间"""
        with self._lock:
            self.last_active[model_name] = time.time()
            logger.debug(f"活跃更新: {model_name} → {datetime.now().strftime('%H:%M:%S')}")

    def get_idle_seconds(self, model_name: str) -> int:
        """获取模型已空闲秒数（-1=从未活跃）"""
        with self._lock:
            t = self.last_active.get(model_name)
        if t is None:
            # 没有活跃记录，用启动时间或当前时间兜底
            started = self.started_at.get(model_name)
            if started:
                return int(time.time() - started)
            return -1
        return int(time.time() - t)

    # -------------------- 按需预热（后台异步拉起）--------------------
    def ensure_started(self, model_name: str) -> bool:
        """
        后台异步拉起模型（不阻塞调用方）。
        策略：首次请求走降级链立即返回，后台拉起供下次用。

        Returns:
            True: 已触发后台拉起 / 已在线
            False: 不需/无法拉起（不受管、无启动命令、auto_start关闭）
        """
        m = self.engine.models.get(model_name)
        if not m or not m.enabled:
            return False
        if not m.auto_start or not m.startup_cmd:
            return False

        # 已在线则无需拉起
        if self.engine.check_model_health(model_name):
            return True

        with self._lock:
            if model_name in self.starting:
                logger.info(f"⏳ {model_name} 正在拉起中，跳过重复触发")
                return True
            self.starting.add(model_name)

        # 后台线程拉起，不阻塞当前请求
        t = threading.Thread(
            target=self._do_startup,
            args=(model_name,),
            daemon=True,
            name=f"start-{model_name}"
        )
        t.start()
        return True

    def _do_startup(self, model_name: str):
        """后台执行拉起流程：执行命令 → 轮询健康检查 → 记录就绪时间"""
        m = self.engine.models.get(model_name)
        cmd = m.startup_cmd if m else ""
        logger.info(f"🚀 后台拉起 {model_name}: {cmd}")
        try:
            log_file = f"/tmp/mlx-startup-{model_name}.log"
            # Popen 非阻塞启动；start_new_session 确保子进程独立运行
            # 对 launchctl kickstart（非阻塞）和 mlx_lm.server（阻塞）都适用
            proc = subprocess.Popen(
                cmd, shell=True,
                stdout=open(log_file, 'w'),
                stderr=subprocess.STDOUT,
                start_new_session=True
            )
            logger.info(f"  启动 PID={proc.pid}, 日志→{log_file}")
        except Exception as e:
            logger.error(f"{model_name} 启动命令异常: {e}")

        # 轮询健康检查直到就绪或超时
        deadline = time.time() + self.startup_timeout
        checks = 0
        while time.time() < deadline:
            checks += 1
            if self._stop_event.is_set():
                break
            time.sleep(self.startup_check_interval)
            if self.engine.check_model_health(model_name):
                with self._lock:
                    self.started_at[model_name] = time.time()
                    self.last_active[model_name] = time.time()  # 启动即视为活跃
                    self.starting.discard(model_name)
                logger.info(f"✅ {model_name} 已就绪（{checks}次检查，"
                            f"耗时约{checks * self.startup_check_interval}s），"
                            f"进入{self.startup_grace}s 宽限期")
                return

        with self._lock:
            self.starting.discard(model_name)
        logger.warning(f"⚠️ {model_name} 拉起后 {self.startup_timeout}s 未就绪"
                       f"（{checks}次检查均离线）")

    # -------------------- 手动控制 --------------------
    def start_model(self, model_name: str) -> tuple[bool, str]:
        """手动拉起模型（同步等待就绪）"""
        m = self.engine.models.get(model_name)
        if not m:
            return False, f"模型 '{model_name}' 不存在"
        if not m.startup_cmd:
            return False, f"模型 '{model_name}' 无启动命令"
        if m.enabled is False:
            return False, f"模型 '{model_name}' 已禁用，请先启用"

        if self.engine.check_model_health(model_name):
            return True, f"{model_name} 已在线，无需拉起"

        logger.info(f"🔧 手动拉起 {model_name}: {m.startup_cmd}")
        try:
            log_file = f"/tmp/mlx-startup-{model_name}.log"
            proc = subprocess.Popen(
                m.startup_cmd, shell=True,
                stdout=open(log_file, 'w'),
                stderr=subprocess.STDOUT,
                start_new_session=True
            )
            logger.info(f"  启动 PID={proc.pid}, 日志→{log_file}")
        except Exception as e:
            return False, f"启动命令执行失败: {e}"

        # 等待就绪（最多 startup_timeout）
        deadline = time.time() + self.startup_timeout
        while time.time() < deadline:
            time.sleep(self.startup_check_interval)
            if self.engine.check_model_health(model_name):
                with self._lock:
                    self.started_at[model_name] = time.time()
                    self.last_active[model_name] = time.time()
                    self.starting.discard(model_name)
                return True, f"{model_name} 拉起成功，已就绪"

        return False, f"{model_name} 启动命令已执行，但 {self.startup_timeout}s 内未就绪（模型可能仍在加载）"

    def stop_model(self, model_name: str) -> tuple[bool, str]:
        """手动卸载模型释放内存"""
        m = self.engine.models.get(model_name)
        if not m:
            return False, f"模型 '{model_name}' 不存在"
        if not m.shutdown_cmd:
            return False, f"模型 '{model_name}' 无卸载命令"

        if not self.engine.check_model_health(model_name):
            with self._lock:
                self.last_unload[model_name] = time.time()
            return True, f"{model_name} 已离线，无需卸载"

        logger.info(f"🛑 手动卸载 {model_name}: {m.shutdown_cmd}")
        try:
            subprocess.run(m.shutdown_cmd, shell=True, timeout=15,
                           capture_output=True, text=True)
        except Exception as e:
            return False, f"卸载命令执行失败: {e}"

        # 确认已卸载（ollama 服务仍在线但模型已移除也算成功）
        time.sleep(3)
        if not self._is_model_loaded(model_name):
            with self._lock:
                self.last_unload[model_name] = time.time()
                self.last_active.pop(model_name, None)
            return True, f"{model_name} 已卸载，内存已释放"
        return False, f"{model_name} 卸载命令已执行，但模型仍加载中"

    # -------------------- 空闲卸载守护 --------------------
    def start_daemon(self):
        """启动空闲扫描守护线程"""
        if not self.enabled:
            logger.info("生命周期管理已禁用，不启动守护线程")
            return
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._idle_scan_loop,
            daemon=True,
            name="lifecycle-daemon"
        )
        self._thread.start()
        logger.info(f"🔄 生命周期守护线程已启动 | 扫描间隔={self.idle_check_interval}s "
                    f"默认空闲超时={self.default_idle_timeout}s")

    def stop_daemon(self):
        """停止守护线程"""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)

    def _idle_scan_loop(self):
        """空闲扫描主循环"""
        logger.info("生命周期守护进入主循环")
        while not self._stop_event.is_set():
            try:
                self._scan_once()
            except Exception as e:
                logger.error(f"空闲扫描异常: {e}")
            self._stop_event.wait(self.idle_check_interval)

    def _scan_once(self):
        """扫一轮：检查所有受管本地模型，空闲超时则卸载"""
        now = time.time()
        actions = []
        for name in list(self.engine.models.keys()):
            if not self._is_managed(name):
                continue

            # 正在拉起中，跳过
            with self._lock:
                if name in self.starting:
                    continue

            # 不在线，无需卸载
            if not self.engine.check_model_health(name):
                continue

            # 宽限期检查：刚拉起的不动
            with self._lock:
                started = self.started_at.get(name)
            if started and now - started < self.startup_grace:
                idle = max(0, int(now - (self.last_active.get(name, started))))
                actions.append(f"[{name}] 宽限中 {int(now-started)}s/{self.startup_grace}s (idle≈{idle}s)")
                continue

            # 计算空闲时间
            idle_sec = self.get_idle_seconds(name)
            timeout = self._get_idle_timeout(name)

            if idle_sec < 0:
                # 从未活跃且无启动记录，用当前时间兜底视为刚启动
                with self._lock:
                    self.last_active[name] = now
                continue

            if idle_sec > timeout:
                # 空闲超时 → 卸载
                ok, msg = self.stop_model(name)
                actions.append(f"[{name}] UNLOADED (idle={idle_sec}s/{timeout}s) → {msg}")
            else:
                actions.append(f"[{name}] idle={idle_sec}s/{timeout}s keep")

        if actions:
            logger.info(" | ".join(actions))

    # -------------------- 状态查询 --------------------
    def get_status(self) -> dict:
        """获取所有模型生命周期状态（供看板展示）"""
        now = time.time()
        models_status = {}

        # 并行健康检查所有 managed 模型（避免9个模型逐一检查太慢）
        managed_names = [n for n in self.engine.models if self._is_managed(n)]
        health_results = {}
        needs_check = []
        for name in managed_names:
            cache_t = self._health_cache.get(name, (0, None))
            if now - cache_t[0] > 5:  # 缓存过期
                needs_check.append(name)
            else:
                health_results[name] = cache_t[1]

        if needs_check:
            with ThreadPoolExecutor(max_workers=9) as ex:
                futures = {ex.submit(self._is_model_loaded, n): n for n in needs_check}
                for f in as_completed(futures):
                    name = futures[f]
                    try:
                        result = f.result()
                    except Exception:
                        result = False
                    health_results[name] = result
                    self._health_cache[name] = (now, result)

        for name, m in self.engine.models.items():
            with self._lock:
                is_starting = name in self.starting
                started = self.started_at.get(name)
                last = self.last_active.get(name)
                unloaded = self.last_unload.get(name)

            timeout = self._get_idle_timeout(name)
            managed = self._is_managed(name)
            healthy = health_results.get(name, m.healthy) if managed else m.healthy

            # 状态判定
            if is_starting:
                state = "starting"
            elif not self._has_lifecycle(name):
                state = "remote"  # 远程不管理
            elif not healthy:
                state = "stopped"
            elif managed and started and now - started < self.startup_grace:
                state = "loading"  # 宽限期装载中
            elif managed:
                idle = self.get_idle_seconds(name)
                if idle >= 0 and timeout > 0:
                    ratio = idle / timeout
                    if ratio > 0.8:
                        state = "idle-warning"  # 即将卸载
                    else:
                        state = "running"
                else:
                    state = "running"
            else:
                state = "running"

            models_status[name] = {
                "state": state,
                "healthy": healthy,
                "enabled": m.enabled,
                "auto_start": getattr(m, 'auto_start', False),
                "managed": managed,
                "idle_timeout": timeout,
                "idle_seconds": self.get_idle_seconds(name),
                "is_starting": is_starting,
                "started_at": datetime.fromtimestamp(started).strftime('%H:%M:%S') if started else None,
                "last_active": datetime.fromtimestamp(last).strftime('%H:%M:%S') if last else None,
                "last_unload": datetime.fromtimestamp(unloaded).strftime('%H:%M:%S') if unloaded else None,
                "startup_cmd": m.startup_cmd,
                "shutdown_cmd": getattr(m, 'shutdown_cmd', ''),
                "cost": m.cost,
                "speed": m.speed,
                "quality": m.quality,
                "description": m.description,
            }

        return {
            "lifecycle_enabled": self.enabled,
            "idle_check_interval": self.idle_check_interval,
            "default_idle_timeout": self.default_idle_timeout,
            "startup_grace": self.startup_grace,
            "daemon_alive": self._thread is not None and self._thread.is_alive(),
            "models": models_status,
        }


# ==================== 模块测试 ====================
if __name__ == "__main__":
    import json
    from router import RouterEngine
    engine = RouterEngine()
    engine.lifecycle_config = (engine.server_config.get('lifecycle', {})) if hasattr(engine, 'lifecycle_config') else {}
    if not hasattr(engine, 'lifecycle_config') or not engine.lifecycle_config:
        # 直接读 yaml
        import yaml
        with open("router_config.yaml") as f:
            cfg = yaml.safe_load(f)
        engine.lifecycle_config = cfg.get('lifecycle', {})

    lm = LifecycleManager(engine)

    print("\n" + "=" * 60)
    print("生命周期引擎测试")
    print("=" * 60)

    # 健康检查
    print("\n--- 模型健康检查 ---")
    engine.check_all_health()

    # 状态
    print("\n--- 生命周期状态 ---")
    status = lm.get_status()
    print(json.dumps(status, indent=2, ensure_ascii=False))
