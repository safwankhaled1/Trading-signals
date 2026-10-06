from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field
from pathlib import Path


def data_directory() -> Path:
    override = os.environ.get("SIGNALDESK_DATA_DIR")
    root = Path(override) if override else Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "GoldSignalDesk"
    root.mkdir(parents=True, exist_ok=True)
    return root


@dataclass
class Settings:
    entry_mode: str = "range"
    entry_margin: float = 1.0
    wait_minutes: float = 10.0
    expiry_enabled: bool = True
    size_mode: str = "fixed"
    fixed_lot: float = 0.03
    risk_percent: float = 1.0
    stop_mode: str = "signal"
    stop_distance: float = 0.50
    targets_mode: str = "manual"
    native_tp_enabled: bool = True
    stages: list = field(default_factory=lambda: [
        {"pips": 50.0, "lot": 0.01}, {"pips": 100.0, "lot": 0.01}, {"pips": 150.0, "lot": 0.01}])
    channel_target_lots: list = field(default_factory=lambda: [0.01, 0.01, 0.01])
    breakeven_enabled: bool = True
    breakeven_stage: int = 1
    breakeven_pips: float = 0.0
    channel_management: bool = False
    follow_edits: bool = False
    management_scope: str = "app"
    selected_tickets: list = field(default_factory=list)
    manage_manual_stops: bool = False
    terminal_path: str = ""
    symbol: str = ""
    channel_id: int = 0
    channel_name: str = ""
    second_channel_id: int = 0
    second_channel_name: str = ""
    shared_channel_settings: bool = True
    pip_size: float = 0.10
    report_utc_offset: float = 0.0
    report_timezone: str = "broker"
    max_price_inference_distance: float = 25.0

    @classmethod
    def from_dict(cls, value):
        known = cls.__dataclass_fields__
        obj = cls(**{k: v for k, v in value.items() if k in known})
        obj.validate()
        return obj

    def validate(self):
        import math
        for name in ("entry_margin", "wait_minutes", "fixed_lot", "risk_percent", "stop_distance", "breakeven_pips", "pip_size"):
            value = float(getattr(self, name))
            if not math.isfinite(value) or value < 0:
                raise ValueError("قيمة غير صالحة: " + name)
            setattr(self, name, value)
        if self.fixed_lot <= 0 or self.stop_distance <= 0 or self.pip_size <= 0:
            raise ValueError("اللوت ومسافة الستوب يجب أن تكون أكبر من صفر")
        if self.expiry_enabled and self.wait_minutes <= 0:
            raise ValueError("مهلة الانتظار يجب أن تكون أكبر من صفر")
        if not 0 < self.risk_percent <= 100:
            raise ValueError("نسبة المخاطرة يجب أن تكون بين صفر و100")
        enums = {"entry_mode": {"range", "direct"}, "size_mode": {"fixed", "risk"}, "stop_mode": {"signal", "fixed"},
                 "targets_mode": {"manual", "channel", "none"}, "management_scope": {"app", "selected", "all"},
                 "report_timezone": {"broker", "local"}}
        for key, values in enums.items():
            if getattr(self, key) not in values:
                raise ValueError("اختيار غير صالح: " + key)
        previous = 0
        for stage in self.stages:
            pips, lot = float(stage["pips"]), float(stage["lot"])
            if not math.isfinite(pips + lot) or pips <= previous or lot <= 0:
                raise ValueError("مراحل الأهداف يجب أن تكون متزايدة وكميات الإغلاق موجبة")
            stage.update(pips=pips, lot=lot)
            previous = pips
        for lot in self.channel_target_lots:
            if not math.isfinite(float(lot)) or float(lot) <= 0:
                raise ValueError("كميات أهداف القناة غير صالحة")
        self.breakeven_stage = int(self.breakeven_stage)
        if self.breakeven_stage < 1:
            raise ValueError("مرحلة التأمين تبدأ من 1")
        self.selected_tickets = [int(t) for t in self.selected_tickets]
        self.channel_id = int(self.channel_id)
        self.second_channel_id = int(self.second_channel_id)
        if self.second_channel_id and not self.channel_id:
            raise ValueError("اختر القناة الأولى قبل إضافة قناة ثانية")
        if self.second_channel_id and self.second_channel_id == self.channel_id:
            raise ValueError("القناة الثانية يجب أن تختلف عن القناة الأولى")
        self.report_utc_offset = float(self.report_utc_offset)
        if not math.isfinite(self.report_utc_offset) or not -14 <= self.report_utc_offset <= 14:
            raise ValueError("فرق توقيت الوسيط يجب أن يكون بين -14 و14 ساعة")

    def to_dict(self):
        return asdict(self)

    def monitored_channels(self):
        return [{"id": identifier, "name": name} for identifier, name in (
            (self.channel_id, self.channel_name), (self.second_channel_id, self.second_channel_name)) if identifier]
