#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
缠论黄金实时监控悬浮框 v2.0 —— 实时计算版
- 通过新浪API获取K线历史数据
- 实时计算缠论走势结构：包含处理→分型→笔→中枢→背驰
- 自动推导三类买卖点信号
"""

import tkinter as tk
from tkinter import messagebox, ttk
import urllib.request
import threading
import time
import json
import os
import sys

# ============================================================
# 标的配置
# ============================================================

SYMBOLS = {
    "hf_XAU":   {"name": "现货黄金", "unit": "美元/兖司", "symbol": "XAU/USD",  "kline": True},
    "hf_XAG":   {"name": "现货白银", "unit": "美元/兖司", "symbol": "XAG/USD",  "kline": True},
    "hf_CL":    {"name": "WTI原油", "unit": "美元/桶",   "symbol": "WTI",      "kline": True},
    "au0":      {"name": "沪金主力", "unit": "元/克",     "symbol": "au0",      "kline": True},
    "ag0":      {"name": "沪银主力", "unit": "元/千克",   "symbol": "ag0",      "kline": True},
    "sc0":      {"name": "原油主力", "unit": "元/桶",     "symbol": "sc0",      "kline": True},
    "sh601899": {"name": "紫金矿业", "unit": "元",       "symbol": "601899",   "kline": True},
    "sh600988": {"name": "赤峰黄金", "unit": "元",       "symbol": "600988",   "kline": True},
    "sh601069": {"name": "西部黄金", "unit": "元",       "symbol": "601069",   "kline": True},
    "sz002716": {"name": "湖南白银", "unit": "元",       "symbol": "002716",   "kline": True},
    "sz161226": {"name": "国投白银", "unit": "元",       "symbol": "161226",   "kline": True},
    "sz001337": {"name": "四川黄金", "unit": "元",       "symbol": "001337",   "kline": True},
    "sz160723": {"name": "嘉实原油", "unit": "元",       "symbol": "160723",   "kline": True},
}
FETCH_ORDER = list(SYMBOLS.keys())

# 多品种联立分析分组
COMMODITY_GROUPS = {
    "黄金": {
        "spot": "hf_XAU",      # 现货黄金
        "domestic": "au0",      # 沪金主力
        "stocks": ["sh601899", "sh600988", "sh601069", "sz001337"],  # 黄金股
        "color": "#FFD700"      # 金色
    },
    "白银": {
        "spot": "hf_XAG",      # 现货白银
        "domestic": "ag0",      # 沪银主力
        "stocks": ["sz002716", "sz161226"],  # 白银股
        "color": "#C0C0C0"      # 银色
    },
    "原油": {
        "spot": "hf_CL",       # WTI原油
        "domestic": "sc0",      # 原油主力
        "stocks": ["sz160723"],  # 原油基金
        "color": "#4CAF50"      # 绿色
    }
}

# 本地K线缓存目录
CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".kline_cache")

# 仓位数据文件
POSITION_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".positions.json")

# ============================================================
# 仓位管理模块
# ============================================================

def load_positions():
    """加载仓位数据"""
    if os.path.exists(POSITION_FILE):
        try:
            with open(POSITION_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def save_positions(positions):
    """保存仓位数据"""
    try:
        with open(POSITION_FILE, "w", encoding="utf-8") as f:
            json.dump(positions, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

def calc_position_advice(code, current_price, signal, positions, mm_intention=None):
    """计算仓位操作建议（缠论信号驱动 + 主力意图修正 + 分批管理）
    
    核心原则（缠论思想）：
    1. 根据信号强度决定操作力度（一买 > 二买 > 一买弱 > 三买）
    2. 分批建仓/加仓：信号越强，比例越高
    3. 卖点优先减仓：一卖必须减仓，一卖弱可观察
    4. 走势结构决定进出，而非盈亏
    5. 主力意图修正：吸筹/护盘时更积极，出货/诱多时更保守
    6. 结合盈亏情况给出止损/止盈建议
    
    返回: {action, amount, reason, color, target_position, stop_loss, advice_detail}
    """
    pos = positions.get(code, {})
    cost_price = pos.get("cost_price", 0)
    position_value = pos.get("position_value", 0)
    
    # 信号分值（缠论买卖点强度层级）
    # 注意：与 synthesize_multitimeframe_signals 中的分值保持一致
    signal_score_map = {
        "★一买": 3, "★二买": 2.5, "★一买弱": 2, "★抢跑多": 1.8, "★二买区": 1.5, "◆三买": 1.3, "●抢跑多": 1.2,
        "▲一卖": -3, "▲二卖": -2.5, "▲一卖弱": -2, "★抢跑空": -1.8, "◆三卖": -1.5, "●抢跑空": -1.2,
        "★强多": 2.8, "★偏多": 1.5, "▲强空": -2.8, "▲偏空": -1.5,
        "●偏多": 0.8, "●偏空": -0.8, "●观望": 0
    }
    score = signal_score_map.get(signal, 0)
    
    # 主力意图修正系数
    mm_ratio_mod = 1.0   # 仓位比例修正
    mm_action_mod = 0    # 信号分值修正
    mm_desc = ""         # 修正说明
    mm_icon = ""         # 意图图标
    
    if mm_intention and isinstance(mm_intention, list) and len(mm_intention) > 0:
        mm_first = mm_intention[0]
        if isinstance(mm_first, dict):
            top_intention = mm_first.get("type", "")
            top_conf = mm_first.get("confidence", 0)
            mm_icon = mm_first.get("icon", "")
            conf_weight = min(top_conf * 1.5, 1.0)  # 置信度越高修正越大
        
            # 多头意图：更积极建仓/加仓
            if top_intention == "吸筹":
                mm_ratio_mod = 1.0 + 0.25 * conf_weight
                mm_action_mod = 0.3 * conf_weight
                mm_desc = f"主力吸筹中，可更积极"
            elif top_intention == "逼空":
                mm_ratio_mod = 1.0 + 0.30 * conf_weight
                mm_action_mod = 0.4 * conf_weight
                mm_desc = f"主力逼空，持仓待涨"
            elif top_intention == "拉升":
                mm_ratio_mod = 1.0 + 0.20 * conf_weight
                mm_action_mod = 0.3 * conf_weight
                mm_desc = f"主力拉升中，持有为主"
            elif top_intention == "护盘":
                mm_ratio_mod = 1.0 + 0.15 * conf_weight
                mm_action_mod = 0.2 * conf_weight
                mm_desc = f"主力护盘，支撑有效"
            elif top_intention == "震荡洗盘":
                mm_ratio_mod = 1.0 + 0.10 * conf_weight
                mm_action_mod = 0.15 * conf_weight
                mm_desc = f"洗盘阶段，逢低可接"
            # 试盘分类处理（多头试盘 vs 空头试盘）
            elif top_intention in ("向上试盘", "低开大阳试盘"):
                mm_ratio_mod = 1.0 + 0.05 * conf_weight
                mm_action_mod = 0.1 * conf_weight
                mm_desc = f"{top_intention}，主力试探上方压力，偏多"
            elif top_intention in ("向下试盘", "高开低走试盘"):
                mm_ratio_mod = 1.0 - 0.15 * conf_weight
                mm_action_mod = -0.15 * conf_weight
                mm_desc = f"{top_intention}，主力试探下方支撑，偏空"
            elif top_intention == "试盘":  # 兼容老数据
                mm_ratio_mod = 1.0 - 0.10 * conf_weight
                mm_action_mod = -0.1 * conf_weight
                mm_desc = f"试盘阶段，轻仓等待"
            elif top_intention == "自然走势":
                mm_ratio_mod = 1.0
                mm_action_mod = 0
                mm_desc = ""
            # 空头意图：更保守建仓/更积极减仓
            elif top_intention == "出货":
                mm_ratio_mod = 1.0 - 0.25 * conf_weight
                mm_action_mod = -0.4 * conf_weight
                mm_desc = f"主力出货，尽快减仓"
            elif top_intention == "诱多":
                mm_ratio_mod = 1.0 - 0.30 * conf_weight
                mm_action_mod = -0.5 * conf_weight
                mm_desc = f"诱多陷阱，切勿追高"
            elif top_intention == "打压":
                mm_ratio_mod = 1.0 - 0.20 * conf_weight
                mm_action_mod = -0.3 * conf_weight
                mm_desc = f"主力打压，回避为主"
            # 量价异常意图：根据性质调整操作
            elif top_intention == "放量滞涨":
                mm_ratio_mod = 1.0 - 0.25 * conf_weight
                mm_action_mod = -0.35 * conf_weight
                mm_desc = f"放量滞涨，出货嫌疑，减仓观望"
            elif top_intention == "缩量上涨":
                mm_ratio_mod = 1.0 - 0.15 * conf_weight
                mm_action_mod = -0.2 * conf_weight
                mm_desc = f"缩量上涨，动能不足，谨慎追高"
            elif top_intention == "放量下跌":
                mm_ratio_mod = 1.0 - 0.30 * conf_weight
                mm_action_mod = -0.4 * conf_weight
                mm_desc = f"放量下跌，抛压沉重，回避为主"
            elif top_intention == "利好不涨":
                mm_ratio_mod = 1.0 - 0.20 * conf_weight
                mm_action_mod = -0.25 * conf_weight
                mm_desc = f"利好不涨，多头乏力，信号存疑"
            elif top_intention == "利空不跌":
                mm_ratio_mod = 1.0 + 0.15 * conf_weight
                mm_action_mod = 0.2 * conf_weight
                mm_desc = f"利空不跌，空头乏力，支撑有效"
            elif top_intention == "地量地价":
                mm_ratio_mod = 1.0 - 0.10 * conf_weight
                mm_action_mod = 0
                mm_desc = f"地量地价，变盘在即，等待方向"
    
    # 修正后的信号分值
    adj_score = score + mm_action_mod
    
    # 持仓上限：现货黄金5万，其余2万
    max_position = 50000 if code == "hf_XAU" else 20000
    remaining_space = max_position - position_value
    position_pct = position_value / max_position * 100 if max_position > 0 else 0
    
    # 盈亏计算
    pnl_pct = (current_price - cost_price) / cost_price * 100 if cost_price > 0 else 0
    pnl_amount = (current_price - cost_price) * position_value / current_price if cost_price > 0 and current_price > 0 else 0
    
    # 辅助：拼接意图修正说明
    def _reason(base):
        if mm_desc:
            return f"{base}；{mm_desc}"
        return base
    
    # 辅助：计算止损/止盈价位
    def _calc_stop_levels():
        """根据中枢位置和盈亏计算止损止盈"""
        if cost_price <= 0:
            return None, None
        # 止损：成本价下方5-8%（根据品种波动性）
        stop_loss_pct = 0.08 if code == "hf_XAU" else 0.05
        stop_loss = cost_price * (1 - stop_loss_pct)
        # 止盈：成本价上方10-15%
        take_profit_pct = 0.15 if code == "hf_XAU" else 0.10
        take_profit = cost_price * (1 + take_profit_pct)
        return stop_loss, take_profit
    
    stop_loss, take_profit = _calc_stop_levels()
    
    # 盈亏数据（所有场景通用）
    pnl_info = {
        "pnl_pct": pnl_pct,
        "pnl_amount": pnl_amount,
        "take_profit": take_profit,
    }
    
    # ============= 无持仓 =============
    if position_value <= 0 or cost_price <= 0:
        if adj_score >= 2.5:  # 强买信号
            if adj_score >= 2.8:
                ratio = 0.35
            else:
                ratio = 0.30
            amount = min(max_position * ratio * mm_ratio_mod, 18000 if code == "hf_XAU" else 7000)
            target_pct = position_pct + amount / max_position * 100
            r = {
                "action": "建仓",
                "amount": amount,
                "reason": _reason(f"{signal}，建仓{amount/10000:.1f}万({ratio*mm_ratio_mod*100:.0f}%)"),
                "color": "#FF4444",
                "target_position": target_pct,
                "stop_loss": stop_loss,
                "advice_detail": f"建议首次建仓{amount/10000:.1f}万，目标仓位{target_pct:.0f}%",
            }
            r.update(pnl_info)
            return r
        elif adj_score >= 1.8:  # 中等信号
            amount = min(max_position * 0.20 * mm_ratio_mod, 10000 if code == "hf_XAU" else 4000)
            target_pct = amount / max_position * 100
            r = {
                "action": "轻仓",
                "amount": amount,
                "reason": _reason(f"{signal}，轻仓{amount/10000:.1f}万(20%)"),
                "color": "#FF6666",
                "target_position": target_pct,
                "stop_loss": stop_loss,
                "advice_detail": f"信号中等，轻仓试探{amount/10000:.1f}万",
            }
            r.update(pnl_info)
            return r
        elif adj_score >= 1.2:  # 弱信号
            amount = min(max_position * 0.12 * mm_ratio_mod, 6000 if code == "hf_XAU" else 2500)
            target_pct = amount / max_position * 100
            r = {
                "action": "试探",
                "amount": amount,
                "reason": _reason(f"{signal}，试探{amount/10000:.1f}万(12%)"),
                "color": "#FF8888",
                "target_position": target_pct,
                "stop_loss": stop_loss,
                "advice_detail": f"信号较弱，小额试探{amount/10000:.1f}万",
            }
            r.update(pnl_info)
            return r
        elif adj_score >= 0.6 and mm_ratio_mod > 1.0:  # 信号偏弱但主力意图多头
            # 主力吸筹/拉升/护盘/逼空时，即使信号偏弱也可小额介入
            amount = min(max_position * 0.08 * mm_ratio_mod, 4000 if code == "hf_XAU" else 1500)
            target_pct = amount / max_position * 100
            r = {
                "action": "轻探",
                "amount": amount,
                "reason": _reason(f"{signal}，主力意图偏多，轻探{amount/10000:.1f}万(8%)"),
                "color": "#FFAAAA",
                "target_position": target_pct,
                "stop_loss": stop_loss,
                "advice_detail": f"信号偏弱但主力意图偏多，小额轻探{amount/10000:.1f}万",
            }
            r.update(pnl_info)
            return r
        else:
            r = {
                "action": "空仓",
                "amount": 0,
                "reason": _reason(f"{signal}，信号不足，观望"),
                "color": "#888888",
                "target_position": 0,
                "stop_loss": None,
                "advice_detail": "等待更强信号再入场",
            }
            r.update(pnl_info)
            return r
    
    # ============= 有持仓 =============
    
    # 辅助：根据盈亏生成策略建议
    def _pnl_strategy():
        """根据当前盈亏状态生成止损止盈策略说明"""
        parts = []
        adj_sl = stop_loss  # 本地副本，避免UnboundLocalError
        if pnl_pct >= 10:
            parts.append(f"盈利丰厚({pnl_pct:+.1f}%)，建议移动止盈锁定利润")
            if adj_sl:
                adj_sl = max(adj_sl, cost_price * 1.05)
        elif pnl_pct >= 5:
            parts.append(f"盈利{pnl_pct:+.1f}%，可上移止损至成本上方保护利润")
        elif pnl_pct >= 0:
            parts.append(f"小幅盈利{pnl_pct:+.1f}%，继续持有")
        elif pnl_pct >= -3:
            parts.append(f"小幅浮亏{pnl_pct:.1f}%，关注支撑位")
        elif pnl_pct >= -8:
            parts.append(f"浮亏{pnl_pct:.1f}%，接近止损位需警惕")
        else:
            parts.append(f"亏损较大{pnl_pct:.1f}%，建议严格执行止损")
        if adj_sl and adj_sl > 0:
            sl_dist = (current_price - adj_sl) / current_price * 100
            parts.append(f"止损{adj_sl:.2f}(距现价{sl_dist:.1f}%)")
        if take_profit and take_profit > 0:
            tp_dist = (take_profit - current_price) / current_price * 100
            parts.append(f"止盈{take_profit:.2f}(距现价{tp_dist:.1f}%)")
        return "；".join(parts), adj_sl
    
    # ---- 强买信号（adj_score >= 2.0）：加仓 ----
    if adj_score >= 2.0:
        if position_pct >= 95:
            pnl_str, stop_loss = _pnl_strategy()
            r = {
                "action": "持有",
                "amount": 0,
                "reason": _reason(f"{signal}，已满仓({position_pct:.0f}%)，盈亏{pnl_pct:+.1f}%"),
                "color": "#FFA500",
                "target_position": position_pct,
                "stop_loss": stop_loss,
                "advice_detail": f"已满仓{position_pct:.0f}%，{pnl_str}",
            }
            r.update(pnl_info)
            return r
        
        # 根据信号强度决定加仓比例
        if adj_score >= 2.8:
            if position_pct < 40:
                ratio = 0.45
            elif position_pct < 70:
                ratio = 0.35
            else:
                ratio = 0.25
        elif adj_score >= 2.5:
            if position_pct < 40:
                ratio = 0.40
            elif position_pct < 70:
                ratio = 0.30
            else:
                ratio = 0.20
        else:
            if position_pct < 40:
                ratio = 0.30
            elif position_pct < 70:
                ratio = 0.20
            else:
                ratio = 0.15
        
        add_amount = min(remaining_space * ratio * mm_ratio_mod, max_position * 0.3)
        target_pct = position_pct + add_amount / max_position * 100
        pnl_str, stop_loss = _pnl_strategy()
        r = {
            "action": "加仓",
            "amount": add_amount,
            "reason": _reason(f"{signal}，加仓{add_amount/10000:.1f}万(仓位{position_pct:.0f}%→{target_pct:.0f}%)，盈亏{pnl_pct:+.1f}%"),
            "color": "#FF4444",
            "target_position": target_pct,
            "stop_loss": stop_loss,
            "advice_detail": f"强买信号，加仓{add_amount/10000:.1f}万至{target_pct:.0f}%；{pnl_str}",
        }
        r.update(pnl_info)
        return r
    
    # ---- 中等买信号（1.2 <= adj_score < 2.0）：小幅加仓或持有 ----
    elif adj_score >= 1.2:
        if position_pct >= 80:
            pnl_str, stop_loss = _pnl_strategy()
            r = {
                "action": "持有",
                "amount": 0,
                "reason": _reason(f"{signal}，仓位{position_pct:.0f}%已重，盈亏{pnl_pct:+.1f}%，持有"),
                "color": "#FFA500",
                "target_position": position_pct,
                "stop_loss": stop_loss,
                "advice_detail": f"仓位较重{position_pct:.0f}%，{pnl_str}",
            }
            r.update(pnl_info)
            return r
        
        add_ratio = 0.20 if adj_score >= 1.5 else 0.15
        add_amount = min(remaining_space * add_ratio * mm_ratio_mod, max_position * 0.15)
        target_pct = position_pct + add_amount / max_position * 100
        pnl_str, stop_loss = _pnl_strategy()
        r = {
            "action": "加仓",
            "amount": add_amount,
            "reason": _reason(f"{signal}，小幅加仓{add_amount/10000:.1f}万(仓位{position_pct:.0f}%→{target_pct:.0f}%)，盈亏{pnl_pct:+.1f}%"),
            "color": "#FF6666",
            "target_position": target_pct,
            "stop_loss": stop_loss,
            "advice_detail": f"信号偏多，小幅加仓{add_amount/10000:.1f}万；{pnl_str}",
        }
        r.update(pnl_info)
        return r
    
    # ---- 强卖信号（adj_score <= -2.0）：必须减仓 ----
    elif adj_score <= -2.0:
        # 根据信号强度决定减仓比例
        if adj_score <= -2.8:
            sell_ratio = 0.50 if position_pct > 50 else 0.40
            reason_extra = "趋势背驰，必须减仓"
        elif adj_score <= -2.5:
            sell_ratio = 0.40 if position_pct > 50 else 0.30
            reason_extra = "顶部确认，减仓保护"
        else:
            sell_ratio = 0.25 if position_pct > 50 else 0.20
            reason_extra = "盘整背驰，谨慎减仓"
        
        # 空头意图时加大减仓力度
        if mm_ratio_mod < 1.0:
            sell_ratio = min(sell_ratio * (2 - mm_ratio_mod), 0.65)
        
        sell_amount = min(position_value * sell_ratio, max_position * 0.35)
        target_pct = position_pct - sell_amount / max_position * 100
        
        # 极端情况建议清仓
        if adj_score <= -2.8 and position_pct <= 30:
            pnl_str, stop_loss = _pnl_strategy()
            r = {
                "action": "清仓",
                "amount": position_value,
                "reason": _reason(f"{signal}，清仓离场({reason_extra})，盈亏{pnl_pct:+.1f}%"),
                "color": "#00AA00",
                "target_position": 0,
                "stop_loss": None,
                "advice_detail": f"强卖信号+仓位较轻，清仓{position_value/10000:.1f}万；{pnl_str}",
            }
            r.update(pnl_info)
            return r
        
        pnl_str, stop_loss = _pnl_strategy()
        r = {
            "action": "减仓",
            "amount": sell_amount,
            "reason": _reason(f"{signal}，减仓{sell_amount/10000:.1f}万(仓位{position_pct:.0f}%→{target_pct:.0f}%)，盈亏{pnl_pct:+.1f}%"),
            "color": "#4CAF50" if adj_score <= -2.5 else "#88AA88",
            "target_position": target_pct,
            "stop_loss": stop_loss,
            "advice_detail": f"{reason_extra}，减仓{sell_amount/10000:.1f}万；{pnl_str}",
        }
        r.update(pnl_info)
        return r
    
    # ---- 中等卖信号（-2.0 < adj_score <= -1.2）：小幅减仓 ----
    elif adj_score <= -1.2:
        if position_pct <= 30:
            pnl_str, stop_loss = _pnl_strategy()
            r = {
                "action": "持有",
                "amount": 0,
                "reason": _reason(f"{signal}，仓位{position_pct:.0f}%较轻，盈亏{pnl_pct:+.1f}%，持有观察"),
                "color": "#FFA500",
                "target_position": position_pct,
                "stop_loss": stop_loss,
                "advice_detail": f"仓位轻{position_pct:.0f}%，{pnl_str}",
            }
            r.update(pnl_info)
            return r
        
        sell_ratio = 0.25 if adj_score <= -1.5 else 0.15
        if mm_ratio_mod < 1.0:
            sell_ratio = min(sell_ratio * (2 - mm_ratio_mod), 0.40)
        sell_amount = min(position_value * sell_ratio, max_position * 0.20)
        target_pct = position_pct - sell_amount / max_position * 100
        pnl_str, stop_loss = _pnl_strategy()
        r = {
            "action": "减仓",
            "amount": sell_amount,
            "reason": _reason(f"{signal}，减仓{sell_amount/10000:.1f}万(仓位{position_pct:.0f}%→{target_pct:.0f}%)，盈亏{pnl_pct:+.1f}%"),
            "color": "#88AA88",
            "target_position": target_pct,
            "stop_loss": stop_loss,
            "advice_detail": f"信号偏空，减仓{sell_amount/10000:.1f}万保护利润；{pnl_str}",
        }
        r.update(pnl_info)
        return r
    
    # ---- 中性信号（-1.2 < adj_score < 1.2）：持有 ----
    else:
        pnl_str, stop_loss = _pnl_strategy()
        r = {
            "action": "持有",
            "amount": 0,
            "reason": _reason(f"{signal}，盈亏{pnl_pct:+.1f}%，仓位{position_pct:.0f}%，持有"),
            "color": "#888888",
            "target_position": position_pct,
            "stop_loss": stop_loss,
            "advice_detail": pnl_str,
        }
        r.update(pnl_info)
        return r


# ============================================================
# 自动模拟交易引擎
# ============================================================

def fmt_price(price):
    """智能格式化价格：根据价格大小自动调整小数位数
    价格>=10: 2位小数
    价格<10: 3位小数
    """
    if price <= 0:
        return "0"
    if price >= 10:
        return f"{price:,.2f}"
    else:
        return f"{price:,.3f}"

AUTO_TRADE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".auto_trade.json")
TRADE_LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".trade_log.json")
TRADE_HISTORY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".trade_history.json")
LEARN_PARAMS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".learn_params.json")
INITIAL_CAPITAL = 10000  # 每个品种初始本金
TRADE_FEE_RATE = 0.004   # 买入手续费率：千分之4（卖出免手续费）
MAX_TRADE_LOG = 50       # 最多保存交易记录条数
LEARN_INTERVAL = 5       # 每新增5笔已完成交易触发一次学习
MAX_TRADE_HISTORY = 200  # 最多保存交易历史记录数

# 学习参数安全边界（防止参数调整过度）
LEARN_BOUNDS = {
    "stop_loss_pct":      (-15, -6),    # 止损百分比范围
    "take_profit_1":      (35, 65),     # 第一止盈阈值范围
    "take_profit_2":      (20, 40),     # 第二止盈阈值范围
    "trailing_stop_pct":  (5, 12),      # 移动止盈回落范围
    "breakeven_trigger":  (10, 20),     # 保本出场触发范围
    "buy_ratio_multiplier": (0.5, 1.5), # 买入比例系数范围
    "signal_adj_limit":   (-0.5, 0.5),  # 信号分值修正范围
}

# 默认学习参数
DEFAULT_LEARN_PARAMS = {
    "last_update": "",
    "total_trades_analyzed": 0,
    "signal_adjustments": {},    # 信号分值修正
    "intention_adjustments": {}, # 意图修正系数
    "stop_loss_pct": -10,        # 止损百分比
    "take_profit_1": 50,         # 第一止盈阈值
    "take_profit_2": 30,         # 第二止盈阈值
    "trailing_stop_pct": 8,      # 移动止盈回落百分比
    "breakeven_trigger": 15,     # 保本出场触发百分比
    "buy_ratio_multiplier": 1.0, # 买入比例系数
    "recent_win_rate": 0,        # 近期胜率
    "recent_avg_pnl": 0,         # 近期平均盈亏
}

# T+0品种：期货、现货（可随时买卖）
T0_CODES = ["hf_XAU", "hf_XAG", "hf_CL", "au0", "ag0", "sc0"]
# T+1品种：股票、基金（今日买入明日才能卖出）
T1_CODES = ["sh601899", "sh600988", "sh601069", "sz002716", "sz161226", "sz001337", "sz160723"]

def load_auto_trade():
    """加载自动交易数据"""
    if os.path.exists(AUTO_TRADE_FILE):
        try:
            with open(AUTO_TRADE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def save_auto_trade(data):
    """保存自动交易数据"""
    try:
        with open(AUTO_TRADE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

def init_auto_trade():
    """初始化自动交易：每个品种1万元本金"""
    data = {}
    for code in SYMBOLS:
        data[code] = {
            "cost_price": 0,      # 持仓均价
            "shares": 0,          # 持仓数量
            "cash": INITIAL_CAPITAL,  # 可用现金
            "initial_capital": INITIAL_CAPITAL,
            "realized_pnl": 0,    # 已实现盈亏
            "max_price": 0,       # 持仓后最高价（用于移动止盈）
            "trade_count": 0,     # 交易次数
            "last_action": "",    # 最后操作
            "buy_date": "",       # 买入日期（用于T+1限制）
        }
    save_auto_trade(data)
    return data

def can_sell_today(code, trade_data):
    """检查该品种今天是否可以卖出
    T+0品种（期货/现货）：随时可卖
    T+1品种（股票/基金）：买入次日才能卖出
    """
    if code in T0_CODES:
        return True  # T+0随时可卖
    
    # T+1品种检查买入日期
    pos = trade_data.get(code, {})
    buy_date = pos.get("buy_date", "")
    if not buy_date:
        return True  # 没有买入日期记录（可能是旧数据或空仓），允许卖出
    
    today = time.strftime("%Y-%m-%d")
    return buy_date != today  # 买入日期不是今天才能卖出

def calc_buy_shares(code, buy_amount, current_price):
    """计算买入数量
    T+0品种（期货/现货）：按实际金额计算，无最小单位限制
    T+1品种（股票/基金）：最低100股（1手），且必须是100的整数倍
    返回：可买入的数量（不足最小单位返回0）
    """
    if current_price <= 0:
        return 0
    
    raw_shares = int(buy_amount / current_price)
    
    if code in T1_CODES:
        # 股票/基金：向下取整到100的整数倍
        lot_shares = (raw_shares // 100) * 100
        return lot_shares  # 不足100股时返回0
    else:
        # 期货/现货：无最小单位限制
        return raw_shares

# ============================================================
# 自我学习引擎
# ============================================================

def load_trade_history():
    """加载已完成交易历史记录"""
    if os.path.exists(TRADE_HISTORY_FILE):
        try:
            with open(TRADE_HISTORY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return []

def save_trade_history(history):
    """保存交易历史（最多保留MAX_TRADE_HISTORY条）"""
    try:
        if len(history) > MAX_TRADE_HISTORY:
            history = history[-MAX_TRADE_HISTORY:]
        with open(TRADE_HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(history, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

def load_learn_params():
    """加载学习参数，不存在则返回默认值"""
    if os.path.exists(LEARN_PARAMS_FILE):
        try:
            with open(LEARN_PARAMS_FILE, "r", encoding="utf-8") as f:
                params = json.load(f)
                # 合并默认值（防止新增字段缺失）
                for k, v in DEFAULT_LEARN_PARAMS.items():
                    if k not in params:
                        params[k] = v
                return params
        except Exception:
            pass
    return dict(DEFAULT_LEARN_PARAMS)

def save_learn_params(params):
    """保存学习参数"""
    try:
        with open(LEARN_PARAMS_FILE, "w", encoding="utf-8") as f:
            json.dump(params, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

def _clamp(value, key):
    """将参数值限制在安全边界内"""
    lo, hi = LEARN_BOUNDS.get(key, (value, value))
    return max(lo, min(hi, value))

def record_completed_trade(code, entry_info, exit_price, exit_reason, shares):
    """记录一笔已完成交易（从买入到完全清仓）
    
    entry_info: 买入时记录的字典 {entry_time, entry_price, entry_signal, entry_mm_intention, entry_mm_confidence, entry_resonance}
    exit_price: 卖出价格
    exit_reason: 卖出原因
    shares: 卖出数量
    """
    if not entry_info or entry_info.get("entry_price", 0) <= 0:
        return
    
    entry_price = entry_info["entry_price"]
    pnl_pct = (exit_price - entry_price) / entry_price * 100
    pnl_amount = (exit_price - entry_price) * shares
    
    # 计算持有天数
    entry_time_str = entry_info.get("entry_time", "")
    try:
        entry_dt = time.strptime(entry_time_str, "%Y-%m-%d %H:%M:%S")
        hold_days = (time.time() - time.mktime(entry_dt)) / 86400
    except Exception:
        hold_days = 0
    
    record = {
        "code": code,
        "entry_time": entry_time_str,
        "exit_time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "entry_price": round(entry_price, 4),
        "exit_price": round(exit_price, 4),
        "shares": shares,
        "entry_signal": entry_info.get("entry_signal", ""),
        "entry_mm_intention": entry_info.get("entry_mm_intention", ""),
        "entry_mm_confidence": entry_info.get("entry_mm_confidence", 0),
        "entry_resonance": entry_info.get("entry_resonance", ""),
        "exit_reason": exit_reason,
        "pnl_pct": round(pnl_pct, 2),
        "pnl_amount": round(pnl_amount, 2),
        "hold_days": round(hold_days, 1),
    }
    
    history = load_trade_history()
    history.append(record)
    save_trade_history(history)
    
    # 检查是否需要触发学习
    if len(history) % LEARN_INTERVAL == 0:
        run_learning()

def run_learning():
    """学习引擎：分析历史交易，调整参数"""
    history = load_trade_history()
    if len(history) < LEARN_INTERVAL:
        return  # 交易次数不足，不学习
    
    params = load_learn_params()
    
    # 只分析最近30笔交易
    recent = history[-30:]
    total = len(recent)
    wins = [t for t in recent if t["pnl_pct"] > 0]
    losses = [t for t in recent if t["pnl_pct"] <= 0]
    win_rate = len(wins) / total if total > 0 else 0
    avg_pnl = sum(t["pnl_pct"] for t in recent) / total if total > 0 else 0
    
    # ---- 1. 信号准确率分析 ----
    signal_stats = {}  # {signal: [pnl_list]}
    for t in recent:
        sig = t.get("entry_signal", "")
        if sig:
            signal_stats.setdefault(sig, []).append(t["pnl_pct"])
    
    signal_adjustments = {}
    for sig, pnls in signal_stats.items():
        avg = sum(pnls) / len(pnls)
        if len(pnls) >= 2:
            if avg > 3:       # 平均盈利>3%：信号可信，上调分值
                adj = min(0.15 + avg * 0.02, LEARN_BOUNDS["signal_adj_limit"][1])
            elif avg > 0:     # 小幅盈利：微调
                adj = 0.05
            elif avg > -3:    # 小幅亏损：下调
                adj = -0.1
            else:             # 大幅亏损：显著下调
                adj = max(-0.15 + avg * 0.02, LEARN_BOUNDS["signal_adj_limit"][0])
            signal_adjustments[sig] = round(adj, 3)
    
    # ---- 2. 意图有效性分析 ----
    intention_stats = {}
    for t in recent:
        intent = t.get("entry_mm_intention", "")
        if intent:
            intention_stats.setdefault(intent, []).append(t["pnl_pct"])
    
    intention_adjustments = {}
    for intent, pnls in intention_stats.items():
        avg = sum(pnls) / len(pnls)
        win_cnt = sum(1 for p in pnls if p > 0)
        intent_wr = win_cnt / len(pnls) if pnls else 0
        if len(pnls) >= 2:
            if intent_wr >= 0.6 and avg > 0:
                adj = round(min(0.1 + avg * 0.01, 0.3), 3)
            elif intent_wr <= 0.3 or avg < -3:
                adj = round(max(-0.15 + avg * 0.01, -0.3), 3)
            else:
                adj = 0
            intention_adjustments[intent] = adj
    
    # ---- 3. 止损止盈优化 ----
    # 分析止损交易：止损后是否经常反弹（说明止损太紧）
    stop_loss_trades = [t for t in recent if "止损" in t.get("exit_reason", "")]
    new_stop_loss = params.get("stop_loss_pct", -10)
    if len(stop_loss_trades) >= 3:
        # 止损交易占比过高（>40%），放宽止损
        if len(stop_loss_trades) / total > 0.4:
            new_stop_loss = max(params["stop_loss_pct"] - 1, LEARN_BOUNDS["stop_loss_pct"][0])
        # 止损交易平均亏损很小（接近止损线），说明止损有效
        avg_sl_pnl = sum(t["pnl_pct"] for t in stop_loss_trades) / len(stop_loss_trades)
        if avg_sl_pnl > -5 and len(stop_loss_trades) / total < 0.2:
            # 止损少且亏损小，可以收紧止损
            new_stop_loss = min(params["stop_loss_pct"] + 0.5, LEARN_BOUNDS["stop_loss_pct"][1])
    new_stop_loss = _clamp(new_stop_loss, "stop_loss_pct")
    
    # 分析止盈交易：止盈后是否继续涨
    tp_trades = [t for t in recent if "止盈" in t.get("exit_reason", "")]
    new_tp1 = params.get("take_profit_1", 50)
    new_tp2 = params.get("take_profit_2", 30)
    if len(tp_trades) >= 2:
        avg_tp_pnl = sum(t["pnl_pct"] for t in tp_trades) / len(tp_trades)
        if avg_tp_pnl > 20:
            # 止盈时盈利很高，说明阈值可能太低，可以提高
            new_tp1 = min(params["take_profit_1"] + 2, LEARN_BOUNDS["take_profit_1"][1])
            new_tp2 = min(params["take_profit_2"] + 1, LEARN_BOUNDS["take_profit_2"][1])
        elif avg_tp_pnl < 10:
            # 止盈时盈利很低，说明还没涨到多少就触发了，降低阈值
            new_tp1 = max(params["take_profit_1"] - 2, LEARN_BOUNDS["take_profit_1"][0])
            new_tp2 = max(params["take_profit_2"] - 1, LEARN_BOUNDS["take_profit_2"][0])
    
    # 移动止盈优化
    new_trailing = params.get("trailing_stop_pct", 8)
    mtp_trades = [t for t in recent if "移动" in t.get("exit_reason", "")]
    if len(mtp_trades) >= 2:
        avg_mtp_pnl = sum(t["pnl_pct"] for t in mtp_trades) / len(mtp_trades)
        if avg_mtp_pnl > 25:
            new_trailing = min(params["trailing_stop_pct"] + 0.5, LEARN_BOUNDS["trailing_stop_pct"][1])
        elif avg_mtp_pnl < 15:
            new_trailing = max(params["trailing_stop_pct"] - 0.5, LEARN_BOUNDS["trailing_stop_pct"][0])
    
    # 保本出场优化
    new_breakeven = params.get("breakeven_trigger", 15)
    be_trades = [t for t in recent if "保本" in t.get("exit_reason", "")]
    if len(be_trades) >= 2:
        # 保本出场次数多，说明利润回吐严重，降低触发阈值
        new_breakeven = max(params["breakeven_trigger"] - 1, LEARN_BOUNDS["breakeven_trigger"][0])
    
    # ---- 4. 买入比例调整 ----
    new_buy_mult = params.get("buy_ratio_multiplier", 1.0)
    last10 = recent[-10:] if len(recent) >= 10 else recent
    last10_wr = sum(1 for t in last10 if t["pnl_pct"] > 0) / len(last10) if last10 else 0
    last10_avg = sum(t["pnl_pct"] for t in last10) / len(last10) if last10 else 0
    
    if last10_wr > 0.6 and last10_avg > 2:
        new_buy_mult = min(params["buy_ratio_multiplier"] + 0.05, LEARN_BOUNDS["buy_ratio_multiplier"][1])
    elif last10_wr < 0.4 or last10_avg < -3:
        new_buy_mult = max(params["buy_ratio_multiplier"] - 0.08, LEARN_BOUNDS["buy_ratio_multiplier"][0])
    
    # ---- 保存学习结果 ----
    params["last_update"] = time.strftime("%Y-%m-%d %H:%M:%S")
    params["total_trades_analyzed"] = total
    params["signal_adjustments"] = signal_adjustments
    params["intention_adjustments"] = intention_adjustments
    params["stop_loss_pct"] = round(new_stop_loss, 1)
    params["take_profit_1"] = round(new_tp1, 1)
    params["take_profit_2"] = round(new_tp2, 1)
    params["trailing_stop_pct"] = round(new_trailing, 1)
    params["breakeven_trigger"] = round(new_breakeven, 1)
    params["buy_ratio_multiplier"] = round(new_buy_mult, 2)
    params["recent_win_rate"] = round(win_rate, 3)
    params["recent_avg_pnl"] = round(avg_pnl, 2)
    
    save_learn_params(params)

def load_trade_log():
    """加载交易记录"""
    if os.path.exists(TRADE_LOG_FILE):
        try:
            with open(TRADE_LOG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return []

def save_trade_log(log_list):
    """保存交易记录（最多保留MAX_TRADE_LOG条）"""
    try:
        # 只保留最近的记录
        if len(log_list) > MAX_TRADE_LOG:
            log_list = log_list[-MAX_TRADE_LOG:]
        with open(TRADE_LOG_FILE, "w", encoding="utf-8") as f:
            json.dump(log_list, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

def append_trade_log(code, code_name, action, price, shares, amount, reason, cash, total_pnl, total_pnl_pct):
    """追加一条交易记录"""
    log_list = load_trade_log()
    record = {
        "time": time.strftime("%m-%d %H:%M:%S"),
        "code": code,
        "name": code_name,
        "action": action,
        "price": round(price, 2),
        "shares": shares,
        "amount": round(amount, 0),
        "reason": reason,
        "cash": round(cash, 0),
        "total_pnl": round(total_pnl, 0),
        "total_pnl_pct": round(total_pnl_pct, 1),
    }
    log_list.append(record)
    save_trade_log(log_list)
    return record

def auto_trade_execute(code, current_price, signal, analysis):
    """执行自动交易决策
    
    基于缠论信号 + 主力意图 + 盈亏状态 自动买卖
    返回: {action, shares, price, reason, cash, position_value, cost_price, realized_pnl, total_pnl}
    """
    if current_price <= 0:
        return None
    
    # 加载交易数据
    trade_data = load_auto_trade()
    if code not in trade_data:
        # 初始化该品种
        trade_data[code] = {
            "cost_price": 0, "shares": 0, "cash": INITIAL_CAPITAL,
            "initial_capital": INITIAL_CAPITAL, "realized_pnl": 0,
            "max_price": 0, "trade_count": 0, "last_action": "",
            "buy_date": ""  # 买入日期（T+1限制）
        }
    
    pos = trade_data[code]
    cost_price = pos["cost_price"]
    shares = pos["shares"]
    cash = pos["cash"]
    realized_pnl = pos["realized_pnl"]
    max_price = pos["max_price"]
    buy_date = pos.get("buy_date", "")  # 买入日期
    entry_info = pos.get("entry_info", {})  # 入场条件（用于学习引擎）
    
    # 加载学习参数
    learn_params = load_learn_params()
    signal_adjustments = learn_params.get("signal_adjustments", {})
    intention_adjustments = learn_params.get("intention_adjustments", {})
    buy_ratio_mult = learn_params.get("buy_ratio_multiplier", 1.0)
    
    # T+1检查：股票/基金今日买入不可卖出
    can_sell = can_sell_today(code, trade_data)
    today_str = time.strftime("%Y-%m-%d")
    
    # 当前持仓市值和总资产
    position_value = shares * current_price
    total_assets = cash + position_value
    
    # 当前盈亏
    unrealized_pnl = (current_price - cost_price) * shares if cost_price > 0 and shares > 0 else 0
    pnl_pct = (current_price - cost_price) / cost_price * 100 if cost_price > 0 else 0
    total_pnl = total_assets - INITIAL_CAPITAL
    total_pnl_pct = total_pnl / INITIAL_CAPITAL * 100
    
    # 获取分析数据
    mm_intention = analysis.get("mm_intention", [])
    mm_type = ""
    mm_conf = 0
    if mm_intention and isinstance(mm_intention, list) and len(mm_intention) > 0:
        mm_first = mm_intention[0]
        if isinstance(mm_first, dict):
            mm_type = mm_first.get("type", "")
            mm_conf = mm_first.get("confidence", 0)
    
    # 信号分值
    signal_score_map = {
        "★一买": 3, "★二买": 2.5, "★一买弱": 2, "★抢跑多": 1.8, "★二买区": 1.5, "◆三买": 1.3, "●抢跑多": 1.2,
        "▲一卖": -3, "▲二卖": -2.5, "▲一卖弱": -2, "★抢跑空": -1.8, "◆三卖": -1.5, "●抢跑空": -1.2,
        "★强多": 2.8, "★偏多": 1.5, "▲强空": -2.8, "▲偏空": -1.5,
        "●偏多": 0.8, "●偏空": -0.8, "●观望": 0
    }
    score = signal_score_map.get(signal, 0)
    
    # 学习参数修正：信号分值调整
    score += signal_adjustments.get(signal, 0)
    
    # 意图修正
    bullish_intentions = ["吸筹", "逼空", "拉升", "护盘", "震荡洗盘", "利空不跌",
                          "向上试盘", "低开大阳试盘"]  # 多头试盘
    bearish_intentions = ["出货", "诱多", "打压", "放量滞涨", "缩量上涨", "放量下跌", "利好不涨",
                          "向下试盘", "高开低走试盘"]  # 空头试盘
    # 中性意图：自然走势、地量地价、老试盘(兼容)
    
    is_bullish_mm = mm_type in bullish_intentions
    is_bearish_mm = mm_type in bearish_intentions
    
    # 学习参数修正：意图有效性调整（通过影响is_bullish_mm/is_bearish_mm的判定权重）
    intent_adj = intention_adjustments.get(mm_type, 0)
    if intent_adj > 0:
        score += intent_adj  # 有效意图增加分值
    elif intent_adj < 0:
        score += intent_adj  # 无效意图降低分值
    
    # 多级别共振检测（从分析数据中获取）
    resonance = analysis.get("synthesized_resonance", "")
    has_resonance_buy = "买点共振" in resonance
    has_resonance_sell = "卖点共振" in resonance
    
    # 支撑阻力位（用于智能止损止盈）
    support = analysis.get("nearest_support", 0)
    resistance = analysis.get("nearest_resistance", 0)
    
    action = "持有"
    trade_shares = 0
    reason = ""
    
    # ============= 止盈止损检查（优先级最高）=============
    # T+1限制：股票/基金今日买入不可卖出
    t1_blocked = not can_sell
    
    # 学习参数：止盈止损阈值
    learned_tp1 = learn_params.get("take_profit_1", 50)
    learned_tp2 = learn_params.get("take_profit_2", 30)
    learned_trailing = learn_params.get("trailing_stop_pct", 8) / 100.0
    learned_breakeven = learn_params.get("breakeven_trigger", 15)
    
    # 自适应止损：使用学习参数（默认-10%）
    learned_stop = learn_params.get("stop_loss_pct", -10)
    stop_loss_pct = learned_stop
    if support > 0 and support < current_price:
        # 支撑位止损：跌破支撑位2%止损
        support_stop_pct = (support * 0.98 - current_price) / current_price * 100
        stop_loss_pct = max(stop_loss_pct, support_stop_pct)  # 取更宽松的
    
    if shares > 0 and pnl_pct <= stop_loss_pct and not t1_blocked:
        trade_shares = shares
        sell_value = trade_shares * current_price
        sell_pnl = (current_price - cost_price) * trade_shares
        cash += sell_value
        realized_pnl += sell_pnl
        shares = 0
        cost_price = 0
        max_price = 0
        action = "止损"
        reason = f"触发止损({pnl_pct:+.1f}%, 阈值{stop_loss_pct:.1f}%)"
    
    elif shares > 0 and pnl_pct <= stop_loss_pct and t1_blocked:
        # T+1限制无法止损
        action = "持有"
        reason = f"T+1限制：今日买入不可卖出，无法止损"
    
    # 分批止盈1：使用学习参数（默认50%）
    elif shares > 0 and pnl_pct >= learned_tp1 and not t1_blocked:
        sell_ratio_tp = 0.5 if has_resonance_buy else 0.33
        trade_shares = max(int(shares * sell_ratio_tp), 1)
        if trade_shares > 0:
            sell_value = trade_shares * current_price
            sell_pnl = (current_price - cost_price) * trade_shares
            cash += sell_value
            realized_pnl += sell_pnl
            shares -= trade_shares
            action = "止盈"
            reason = f"盈利{pnl_pct:+.1f}%，卖出{sell_ratio_tp*100:.0f}%锁利"
    
    # 分批止盈2：使用学习参数（默认30%触发，8%回落）
    elif shares > 0 and pnl_pct >= learned_tp2 and not t1_blocked:
        max_price = max(max_price, current_price)
        if max_price > 0 and (max_price - current_price) / max_price >= learned_trailing:
            trade_shares = shares
            sell_value = trade_shares * current_price
            sell_pnl = (current_price - cost_price) * trade_shares
            cash += sell_value
            realized_pnl += sell_pnl
            shares = 0
            cost_price = 0
            max_price = 0
            action = "移动止盈"
            reason = f"盈利{pnl_pct:+.1f}%，从最高{max_price:.2f}回落{learn_params.get('trailing_stop_pct', 8):.0f}%"
    
    # 保本止损：使用学习参数（默认15%）
    elif shares > 0 and max_price > 0 and not t1_blocked:
        max_pnl_pct = (max_price - cost_price) / cost_price * 100 if cost_price > 0 else 0
        if max_pnl_pct >= learned_breakeven and pnl_pct <= 2:
            trade_shares = shares
            sell_value = trade_shares * current_price
            sell_pnl = (current_price - cost_price) * trade_shares
            cash += sell_value
            realized_pnl += sell_pnl
            shares = 0
            cost_price = 0
            max_price = 0
            action = "保本出场"
            reason = f"曾盈利{max_pnl_pct:.1f}%回落至{pnl_pct:.1f}%"
    
    # ============= 卖出信号 =============
    elif score <= -2.0 and shares > 0 and not t1_blocked:
        # 强卖信号：卖出50-80%
        if score <= -2.8:
            sell_ratio = 0.80
        elif score <= -2.5:
            sell_ratio = 0.60
        else:
            sell_ratio = 0.50
        if is_bearish_mm or has_resonance_sell:
            sell_ratio = min(sell_ratio + 0.15, 0.95)
        
        trade_shares = int(shares * sell_ratio)
        if trade_shares > 0:
            sell_value = trade_shares * current_price
            sell_pnl = (current_price - cost_price) * trade_shares
            cash += sell_value
            realized_pnl += sell_pnl
            shares -= trade_shares
            if shares == 0:
                cost_price = 0
                max_price = 0
            action = "减仓"
            reason = f"{signal}，卖出{sell_ratio*100:.0f}%"
    
    elif score <= -1.2 and shares > 0 and (is_bearish_mm or has_resonance_sell) and not t1_blocked:
        # 中等卖信号+空头意图/共振：卖出30%
        trade_shares = int(shares * 0.30)
        if trade_shares > 0:
            sell_value = trade_shares * current_price
            sell_pnl = (current_price - cost_price) * trade_shares
            cash += sell_value
            realized_pnl += sell_pnl
            shares -= trade_shares
            if shares == 0:
                cost_price = 0
                max_price = 0
            action = "减仓"
            reason = f"{signal}+{mm_type}，卖出30%"
    
    elif t1_blocked and shares > 0 and score <= -1.2:
        # T+1限制无法卖出
        action = "持有"
        reason = f"T+1限制：今日买入不可卖出"
    
    # ============= 买入信号 =============
    elif score >= 2.5 and cash > INITIAL_CAPITAL * 0.1:
        # 强买信号：买入50-70%可用资金（共振时加大）*学习系数
        if score >= 2.8:
            buy_ratio = 0.70 if (is_bullish_mm or has_resonance_buy) else 0.50
        else:
            buy_ratio = 0.55 if (is_bullish_mm or has_resonance_buy) else 0.40
        buy_ratio *= buy_ratio_mult  # 学习参数调整
        
        buy_amount = cash * buy_ratio
        buy_shares = calc_buy_shares(code, buy_amount, current_price)
        if buy_shares > 0:
            buy_cost = buy_shares * current_price
            buy_fee = buy_cost * TRADE_FEE_RATE
            total_cost = buy_cost + buy_fee
            if shares > 0:
                total_shares = shares + buy_shares
                cost_price = (cost_price * shares + total_cost) / total_shares
                shares = total_shares
            else:
                shares = buy_shares
                cost_price = total_cost / buy_shares
            cash -= total_cost
            max_price = current_price
            buy_date = today_str  # 记录买入日期（T+1限制）
            # 记录入场条件（学习引擎用）
            entry_info = {
                "entry_time": time.strftime("%Y-%m-%d %H:%M:%S"),
                "entry_price": current_price,
                "entry_signal": signal,
                "entry_mm_intention": mm_type,
                "entry_mm_confidence": mm_conf,
                "entry_resonance": resonance,
            }
            action = "加仓" if shares > buy_shares else "建仓"
            reason = f"{signal}，买入{buy_shares}单位"
    
    elif score >= 1.5 and cash > INITIAL_CAPITAL * 0.15:
        # 中等买信号：买入25-40%（空仓或已持仓均可加仓）*学习系数
        buy_ratio = 0.40 if is_bullish_mm else 0.25
        if shares > 0 and pnl_pct > 0:
            buy_ratio = min(buy_ratio, 0.20)  # 已盈利时少量加仓
        buy_ratio *= buy_ratio_mult  # 学习参数调整
        buy_amount = cash * buy_ratio
        buy_shares = calc_buy_shares(code, buy_amount, current_price)
        if buy_shares > 0:
            buy_cost = buy_shares * current_price
            buy_fee = buy_cost * TRADE_FEE_RATE
            total_cost = buy_cost + buy_fee
            if shares > 0:
                total_shares = shares + buy_shares
                cost_price = (cost_price * shares + total_cost) / total_shares
                shares = total_shares
                action = "加仓"
            else:
                shares = buy_shares
                cost_price = total_cost / buy_shares
                action = "建仓"
            cash -= total_cost
            max_price = current_price
            buy_date = today_str  # 记录买入日期（T+1限制）
            # 记录入场条件（学习引擎用）
            entry_info = {
                "entry_time": time.strftime("%Y-%m-%d %H:%M:%S"),
                "entry_price": current_price,
                "entry_signal": signal,
                "entry_mm_intention": mm_type,
                "entry_mm_confidence": mm_conf,
                "entry_resonance": resonance,
            }
            reason = f"{signal}，买入{buy_shares}单位"
    
    elif score >= 0.8 and cash > INITIAL_CAPITAL * 0.2 and shares == 0:
        # 弱买信号+空仓：小额试探 *学习系数
        buy_ratio = 0.20 if is_bullish_mm else 0.12
        buy_ratio *= buy_ratio_mult  # 学习参数调整
        buy_amount = cash * buy_ratio
        buy_shares = calc_buy_shares(code, buy_amount, current_price)
        if buy_shares > 0:
            buy_cost = buy_shares * current_price
            buy_fee = buy_cost * TRADE_FEE_RATE  # 手续费
            total_cost = buy_cost + buy_fee
            shares = buy_shares
            cost_price = total_cost / buy_shares  # 均价包含手续费
            cash -= total_cost
            max_price = current_price
            buy_date = today_str  # 记录买入日期（T+1限制）
            # 记录入场条件（学习引擎用）
            entry_info = {
                "entry_time": time.strftime("%Y-%m-%d %H:%M:%S"),
                "entry_price": current_price,
                "entry_signal": signal,
                "entry_mm_intention": mm_type,
                "entry_mm_confidence": mm_conf,
                "entry_resonance": resonance,
            }
            action = "试探"
            reason = f"{signal}，试探买入{buy_shares}单位"
    
    # 更新max_price
    if shares > 0:
        max_price = max(max_price, current_price)
    
    # 计算最终状态
    position_value = shares * current_price
    total_assets = cash + position_value
    unrealized_pnl = (current_price - cost_price) * shares if cost_price > 0 and shares > 0 else 0
    total_pnl = total_assets - INITIAL_CAPITAL
    total_pnl_pct = total_pnl / INITIAL_CAPITAL * 100
    
    # 保存交易数据
    # 清仓时记录交易历史（学习引擎用）
    old_shares = pos["shares"]
    if old_shares > 0 and shares == 0 and entry_info:
        record_completed_trade(code, entry_info, current_price, action, old_shares)
        entry_info = {}  # 清仓后清空入场信息
    
    trade_data[code] = {
        "cost_price": cost_price,
        "shares": shares,
        "cash": cash,
        "initial_capital": INITIAL_CAPITAL,
        "realized_pnl": realized_pnl,
        "max_price": max_price,
        "trade_count": pos["trade_count"] + (1 if action not in ["持有", "--"] else 0),
        "last_action": action,
        "buy_date": buy_date if shares > 0 else "",  # 空仓时清空买入日期
        "entry_info": entry_info if shares > 0 else {},  # 空仓时清空入场信息
    }
    save_auto_trade(trade_data)
    
    # 记录交易日志（只有实际交易才记录）
    if action not in ["持有", "--", ""]:
        code_name = SYMBOLS.get(code, {}).get("name", code)
        trade_amount = trade_shares * current_price if trade_shares > 0 else position_value
        append_trade_log(
            code, code_name, action, current_price,
            trade_shares if trade_shares > 0 else shares,
            trade_amount, reason, cash, total_pnl, total_pnl_pct
        )
    
    return {
        "action": action,
        "shares": trade_shares if trade_shares > 0 else shares,
        "price": current_price,
        "reason": reason,
        "cash": cash,
        "position_value": position_value,
        "cost_price": cost_price,
        "realized_pnl": realized_pnl,
        "unrealized_pnl": unrealized_pnl,
        "total_pnl": total_pnl,
        "total_pnl_pct": total_pnl_pct,
        "total_assets": total_assets,
    }


# ============================================================
# K线数据获取模块
# ============================================================

def fetch_kline_sina(symbol, scale=240, datalen=120):
    """通过新浪财经API获取K线数据 (scale: 240=日K, 1680=周K, 60=60分钟)"""
    url = (f"https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
           f"CN_MarketData.getKLineData?symbol={symbol}&scale={scale}&ma=no&datalen={datalen}")
    req = urllib.request.Request(url, headers={
        "Referer": "https://finance.sina.com.cn",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    })
    try:
        resp = urllib.request.urlopen(req, timeout=10)
        raw = resp.read().decode("utf-8")
        if raw and raw != "null":
            data = json.loads(raw)
            return [{"day": d["day"], "open": float(d["open"]), "high": float(d["high"]),
                     "low": float(d["low"]), "close": float(d["close"]),
                     "volume": float(d["volume"])} for d in data]
    except Exception:
        pass
    return None


def load_kline_cache(symbol, scale, suffix=""):
    """加载本地K线缓存"""
    os.makedirs(CACHE_DIR, exist_ok=True)
    cache_file = os.path.join(CACHE_DIR, f"{symbol}_{scale}{suffix}.json")
    if os.path.exists(cache_file):
        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return None


def save_kline_cache(symbol, scale, data, suffix=""):
    """保存K线缓存"""
    os.makedirs(CACHE_DIR, exist_ok=True)
    cache_file = os.path.join(CACHE_DIR, f"{symbol}_{scale}{suffix}.json")
    try:
        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception:
        pass


def get_kline_data(symbol, scale=240, datalen=120):
    """获取K线数据（优先API，失败则用缓存）"""
    if symbol.startswith("hf_"):
        # 国际品种：合并历史数据(akshare) + 今日数据(实时积累)
        history = load_kline_cache(symbol, scale, suffix="_history")
        today_data = load_kline_cache(symbol, scale, suffix="_today")
        
        # 尝试从akshare获取最新历史数据
        fresh_history = fetch_kline_akshare(symbol)
        if fresh_history and len(fresh_history) > 10:
            save_kline_cache(symbol, scale, fresh_history, suffix="_history")
            history = fresh_history
        
        # 合并历史 + 今日
        if history:
            merged = list(history)
            if today_data:
                # 今日数据的日期如果已在历史中，替换；否则追加
                today_date = today_data[0]["day"] if today_data else None
                if today_date and merged and merged[-1]["day"] == today_date:
                    merged[-1] = today_data[0]
                elif today_date:
                    merged.append(today_data[0])
            return merged[-500:]  # 最多保留500条
        
        # 回退：只使用历史或今日数据
        if history:
            return history
        if today_data:
            return today_data
        return None
    elif symbol in ("au0", "ag0", "sc0"):
        # 国内期货：使用akshare获取日K/周K
        data = fetch_kline_domestic_akshare(symbol, scale)
        if data and len(data) > 10:
            save_kline_cache(symbol, scale, data)
            return data
        cached = load_kline_cache(symbol, scale)
        if cached and len(cached) > 10:
            return cached
        return None
    else:
        # A股用新浪
        data = fetch_kline_sina(symbol, scale, datalen)
        if data and len(data) > 10:
            save_kline_cache(symbol, scale, data)
            return data
    cached = load_kline_cache(symbol, scale)
    if cached and len(cached) > 10:
        return cached
    return None


def fetch_kline_akshare(symbol):
    """通过akshare获取国际品种K线数据"""
    try:
        import akshare as ak
        # hf_XAU -> XAU, hf_GC -> GC
        ak_symbol = symbol.replace("hf_", "")
        df = ak.futures_foreign_hist(symbol=ak_symbol)
        if df is not None and len(df) > 0:
            klines = []
            for _, row in df.iterrows():
                klines.append({
                    "day": str(row['date'])[:10],
                    "open": float(row['open']),
                    "high": float(row['high']),
                    "low": float(row['low']),
                    "close": float(row['close']),
                    "volume": str(int(row['volume']))
                })
            return klines[-300:]  # 只保留最近300条
    except Exception:
        pass
    return None


def fetch_kline_domestic_akshare(symbol, scale=240):
    """通过akshare获取国内期货日K/周K数据
    symbol: au0=沪金, ag0=沪银, sc0=原油
    scale: 240=日K, 1680=周K
    """
    try:
        import akshare as ak
        # 使用新浪期货日线接口
        period = "daily" if scale == 240 else "weekly"
        df = ak.futures_zh_daily_sina(symbol=symbol)
        if df is not None and len(df) > 0:
            klines = []
            for _, row in df.iterrows():
                klines.append({
                    "day": str(row['date'])[:10],
                    "open": float(row['open']),
                    "high": float(row['high']),
                    "low": float(row['low']),
                    "close": float(row['close']),
                    "volume": str(int(row.get('volume', 0)))
                })
            return klines[-300:]
    except Exception:
        pass
    return None


def fetch_kline_minute_sina(symbol, scale=60, datalen=100):
    """通过新浪获取A股分钟K线 (scale: 60=60分钟, 30=30分钟)"""
    url = (f"https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
           f"CN_MarketData.getKLineData?symbol={symbol}&scale={scale}&ma=no&datalen={datalen}")
    req = urllib.request.Request(url, headers={
        "Referer": "https://finance.sina.com.cn",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    })
    try:
        resp = urllib.request.urlopen(req, timeout=10)
        raw = resp.read().decode("utf-8")
        if raw and raw != "null":
            data = json.loads(raw)
            klines = [{"day": d["day"], "open": float(d["open"]), "high": float(d["high"]),
                     "low": float(d["low"]), "close": float(d["close"]),
                     "volume": float(d["volume"])} for d in data]
            # 缓存到本地
            save_kline_cache(symbol, scale, klines)
            return klines
    except Exception:
        pass
    # API失败则用缓存
    return load_kline_cache(symbol, scale)


def fetch_kline_minute_akshare(period="60", symbol="au0"):
    """通过akshare获取品种分钟K线
    symbol: au0=沪金, ag0=沪银, sc0=原油
    """
    try:
        import akshare as ak
        df = ak.futures_zh_minute_sina(symbol=symbol, period=period)
        if df is not None and len(df) > 0:
            klines = []
            for _, row in df.iterrows():
                klines.append({
                    "day": str(row['datetime']),
                    "open": float(row['open']),
                    "high": float(row['high']),
                    "low": float(row['low']),
                    "close": float(row['close']),
                    "volume": str(int(row['volume']))
                })
            # 缓存到本地
            save_kline_cache(symbol, int(period), klines)
            return klines[-200:]  # 保留最近200条
    except Exception:
        pass
    # API失败则用缓存
    return load_kline_cache(symbol, int(period))


# 内存缓存：减少磁盘IO
_accumulate_cache = {}

def accumulate_gold_kline(price, high, low, open_p, symbol="hf_XAU"):
    """为国际品种积累日K线数据（存储到独立的_today文件）"""
    global _accumulate_cache
    today = time.strftime("%Y-%m-%d")
    
    # 从内存缓存获取
    if symbol in _accumulate_cache:
        data = _accumulate_cache[symbol]
    else:
        # 从 _today.json 加载（只包含今日数据）
        data = load_kline_cache(symbol, 240, suffix="_today") or []
        if data:
            print(f"[积累K线] {symbol} 从今日缓存加载")
        else:
            print(f"[积累K线] {symbol} 开始积累今日数据")
    
    # 检查是否需要更新
    need_save = False
    if data and isinstance(data[-1], dict) and "day" in data[-1] and data[-1]["day"] == today:
        # 今天已有数据，检查价格变化是否显著（>0.1%）
        old_close = data[-1].get("close", 0)
        if old_close > 0 and abs(price - old_close) / old_close > 0.001:
            data[-1] = {"day": today, "open": open_p, "high": high, "low": low,
                        "close": price, "volume": 0}
            need_save = True
    else:
        # 新的一天或数据格式不正确，清空并写入今日数据
        data = [{"day": today, "open": open_p, "high": high, "low": low,
                 "close": price, "volume": 0}]
        need_save = True
        print(f"[积累K线] {symbol} 新增 {today} 记录")
    
    # 更新内存缓存
    _accumulate_cache[symbol] = data
    
    # 只在必要时写入磁盘（_today文件）
    if need_save:
        os.makedirs(CACHE_DIR, exist_ok=True)
        save_kline_cache(symbol, 240, data, suffix="_today")
    
    return data


# 内存缓存：小时级别K线积累
_hourly_cache = {}
_hourly_current = {}  # 当前未完成的1小时K线

def accumulate_hourly_kline(price, symbol="hf_XAU"):
    """用实时价格积累小时级别K线（国际品种）
    
    每5秒调用一次，实时更新当前小时的OHLC
    当小时变化时，将上一小时存入历史数据
    """
    global _hourly_cache, _hourly_current
    if price <= 0:
        return None
    
    now = time.time()
    # 当前小时的起始时间戳（整点）
    current_hour_ts = int(now // 3600) * 3600
    current_hour_str = time.strftime("%Y-%m-%d %H:00", time.localtime(current_hour_ts))
    
    cache_key = f"{symbol}_1h"
    
    # 初始化缓存
    if cache_key not in _hourly_cache:
        _hourly_cache[cache_key] = load_kline_cache(symbol, 60, suffix="_hourly") or []
        print(f"[小时K线] {symbol} 初始化积累，已有{len(_hourly_cache[cache_key])}条历史记录")
    
    # 获取或创建当前小时的K线
    if cache_key not in _hourly_current or _hourly_current[cache_key]["hour_ts"] != current_hour_ts:
        # 新的一小时：将上一小时存入历史
        if cache_key in _hourly_current:
            prev_hour = _hourly_current[cache_key]
            history = _hourly_cache[cache_key]
            # 避免重复（检查history是否为空或格式是否正确）
            if not history or not isinstance(history[-1], dict) or "day" not in history[-1] or history[-1]["day"] != prev_hour["hour_str"]:
                history.append({
                    "day": prev_hour["hour_str"],
                    "open": prev_hour["open"],
                    "high": prev_hour["high"],
                    "low": prev_hour["low"],
                    "close": prev_hour["close"],
                    "volume": 0
                })
                # 保留最近1000条（约41天）
                _hourly_cache[cache_key] = history[-1000:]
                print(f"[小时K线] {symbol} 完成 {prev_hour['hour_str']}，当前共{len(history)}条")
        # 创建新的小时K线
        _hourly_current[cache_key] = {
            "hour_ts": current_hour_ts,
            "hour_str": current_hour_str,
            "open": price,
            "high": price,
            "low": price,
            "close": price
        }
    else:
        # 同一小时内，更新OHLC
        cur = _hourly_current[cache_key]
        cur["high"] = max(cur["high"], price)
        cur["low"] = min(cur["low"], price)
        cur["close"] = price
    
    return _hourly_cache[cache_key]


def get_accumulated_hourly_klines(symbol="hf_XAU"):
    """获取积累的小时级别K线数据（包含当前未完成的小时）
    
    返回: 1h, 2h, 4h K线数据
    """
    cache_key = f"{symbol}_1h"
    history = _hourly_cache.get(cache_key, [])
    current = _hourly_current.get(cache_key)
    
    # 合并历史 + 当前小时
    klines_1h = list(history)
    if current:
        klines_1h.append({
            "day": current["hour_str"],
            "open": current["open"],
            "high": current["high"],
            "low": current["low"],
            "close": current["close"],
            "volume": 0
        })
    
    if len(klines_1h) < 10:
        return None, None, None
    
    # 聚合为2小时和4小时
    klines_2h = aggregate_hourly_klines(klines_1h, 2)
    klines_4h = aggregate_hourly_klines(klines_1h, 4)
    
    return klines_1h, klines_2h, klines_4h


def aggregate_hourly_klines(klines, factor):
    """将1小时K线聚合为N小时K线"""
    if not klines or factor <= 1:
        return klines
    result = []
    for i in range(0, len(klines), factor):
        group = klines[i:i+factor]
        if group:
            result.append({
                "day": group[0]["day"],
                "open": group[0]["open"],
                "high": max(k["high"] for k in group),
                "low": min(k["low"] for k in group),
                "close": group[-1]["close"],
                "volume": 0
            })
    return result


def save_hourly_cache(symbol):
    """将小时级别缓存写入磁盘"""
    cache_key = f"{symbol}_1h"
    history = _hourly_cache.get(cache_key, [])
    if history:
        save_kline_cache(symbol, 60, history, suffix="_hourly")


def synthesize_multitimeframe_signals(timeframe_results, current_price, is_international=False):
    """多级别信号合成（缠论区间套原理）
    
    大级别定方向，小级别找入场
    权重: 高级别时间框架权重更大
    is_international: 国际品种只使用日线+计算支撑阻力（因为小时数据来自国内期货，价格单位不同）
    """
    if not timeframe_results:
        return None
    
    # 时间级别权重 (高级别权重更大)
    tf_weights = {
        "1h": 1,
        "2h": 2,
        "4h": 3,
        "daily": 5,
        "weekly": 8,
        "monthly": 10
    }
    
    # 信号分值映射（与calc_position_advice保持一致）
    signal_scores = {
        "★一买": 3, "★二买": 2.5, "★一买弱": 2, "★抢跑多": 1.8, "★二买区": 1.5, "◆三买": 1.3, "●抢跑多": 1.2,
        "▲一卖": -3, "▲二卖": -2.5, "▲一卖弱": -2, "★抢跑空": -1.8, "◆三卖": -1.5, "●抢跑空": -1.2,
        "★强多": 2.8, "★偏多": 1.5, "▲强空": -2.8, "▲偏空": -1.5,
        "●偏多": 0.8, "●偏空": -0.8, "●观望": 0
    }
    
    # 趋势分值
    trend_scores = {
        "up": 1,
        "down": -1,
        "consolidation": 0,
        "unknown": 0
    }
    
    total_score = 0
    total_weight = 0
    buy_count = 0
    sell_count = 0
    
    # 收集各时间级别的中枢信息
    all_pivots_by_tf = {}
    
    for tf_name, result in timeframe_results.items():
        if not result:
            continue
        weight = tf_weights.get(tf_name, 1)
        
        # 信号分
        signal = result.get("signal", "●观望")
        if signal in signal_scores:
            score = signal_scores[signal]
        else:
            # 根据趋势判断
            trend = result.get("trend", "unknown")
            score = trend_scores.get(trend, 0)
        
        total_score += score * weight
        total_weight += weight
        
        # 统计买卖点
        if "买" in signal:
            buy_count += 1
        if "卖" in signal:
            sell_count += 1
        
        # 收集中枢信息（所有级别均参与支撑阻力计算）
        pivots = result.get("all_pivots_info", [])
        if pivots:
            all_pivots_by_tf[tf_name] = result.get("all_pivots", [])
    
    if total_weight == 0:
        return None
    
    # 计算加权平均分 (-3 ~ +3)
    avg_score = total_score / total_weight
    
    # 共振检测: 多个级别同时出现买点/卖点
    resonance = ""
    if buy_count >= 3:
        resonance = "多级别买点共振"
    elif sell_count >= 3:
        resonance = "多级别卖点共振"
    
    # 根据平均分确定主信号
    if avg_score >= 2.0:
        main_signal = "★一买"
        main_color = "#FF0000"
        desc = f"多级别共振强买信号 (综合评分:{avg_score:.1f})"
    elif avg_score >= 1.0:
        main_signal = "★二买"
        main_color = "#FF4444"
        desc = f"多级别偏多 (综合评分:{avg_score:.1f})"
    elif avg_score <= -2.0:
        main_signal = "▲一卖"
        main_color = "#00AA00"
        desc = f"多级别共振强卖信号 (综合评分:{avg_score:.1f})"
    elif avg_score <= -1.0:
        main_signal = "▲二卖"
        main_color = "#44AA44"
        desc = f"多级别偏空 (综合评分:{avg_score:.1f})"
    elif avg_score > 0:
        main_signal = "●偏多"
        main_color = "#FF8888"
        desc = f"综合偏多 (评分:{avg_score:.1f})"
    elif avg_score < 0:
        main_signal = "●偏空"
        main_color = "#88AA88"
        desc = f"综合偏空 (评分:{avg_score:.1f})"
    else:
        main_signal = "●观望"
        main_color = "#888888"
        desc = f"多级别震荡 (评分:{avg_score:.1f})"
    
    # 添加共振信息
    if resonance:
        desc = f"{resonance}! {desc}"
    
    # 计算多级别支撑阻力
    multi_support, multi_resistance = calc_multitimeframe_support_resistance(
        all_pivots_by_tf, current_price
    )
    
    return {
        "signal": main_signal,
        "signal_color": main_color,
        "signal_desc": desc,
        "score": avg_score,
        "resonance": resonance,
        "buy_count": buy_count,
        "sell_count": sell_count,
        "multi_support": multi_support,
        "multi_resistance": multi_resistance
    }


def synthesize_mm_intention(timeframe_results):
    """综合各级别主力意图，大级别权重更大
    
    将各时间级别检测到的主力意图进行加权汇总，
    返回综合后的主力意图列表（最多2个）。
    如果所有级别均无明显意图，返回"自然走势"。
    """
    # 时间级别权重（大级别定性质，权重更大）
    tf_weights = {
        "monthly": 8, "weekly": 5, "daily": 3,
        "4h": 2, "2h": 1.5, "1h": 1
    }
    
    # 汇总每种意图类型的加权得分
    intention_scores = {}  # {意图类型: {"score": float, "sources": [], "color": str, "icon": str}}
    
    for tf_name, result in timeframe_results.items():
        if not result:
            continue
        weight = tf_weights.get(tf_name, 1)
        tf_intentions = result.get("mm_intention", [])
        
        for intention in tf_intentions:
            if not isinstance(intention, dict):
                continue
            itype = intention.get("type", "")
            conf = intention.get("confidence", 0)
            weighted_score = conf * weight
            
            if itype not in intention_scores:
                intention_scores[itype] = {
                    "score": 0,
                    "sources": [],
                    "color": intention.get("color", "#888888"),
                    "icon": intention.get("icon", "")
                }
            intention_scores[itype]["score"] += weighted_score
            tf_display = {"monthly": "月", "weekly": "周", "daily": "日",
                         "4h": "4h", "2h": "2h", "1h": "1h"}.get(tf_name, tf_name)
            intention_scores[itype]["sources"].append(tf_display)
    
    if not intention_scores:
        # 所有级别均无明显意图
        return [{
            "type": "自然走势",
            "confidence": 0.5,
            "desc": "各级别未检测到明显主力操控痕迹，市场按自然供需运行",
            "color": "#888888",
            "icon": "➖",
            "sources": []
        }]
    
    # 按加权得分排序，取最高分的唯一意图
    sorted_intentions = sorted(intention_scores.items(), key=lambda x: -x[1]["score"])
    
    # 计算最大可能得分（用于归一化置信度）
    max_possible = sum(tf_weights.values())  # 所有级别都是同一意图时的满分
    
    results = []
    for itype, info in sorted_intentions[:1]:  # 只取第1名
        # 归一化置信度到 0-1
        normalized_conf = min(info["score"] / max(max_possible * 0.5, 1), 0.95)
        normalized_conf = max(normalized_conf, 0.15)  # 至少显示一点
        
        # 构建来源说明
        src_text = "、".join(info["sources"])
        desc = f"{src_text}级别共振"
        
        results.append({
            "type": itype,
            "confidence": normalized_conf,
            "desc": desc,
            "color": info["color"],
            "icon": info["icon"],
            "sources": info["sources"]
        })
    
    return results


def calc_multitimeframe_support_resistance(all_pivots_by_tf, current_price):
    """多级别支撑阻力计算（缠论区间套）
    
    大级别中枢边界 = 强支撑/阻力
    小级别中枢边界 = 弱支撑/阻力
    """
    supports = []
    resistances = []
    
    # 时间级别权重（用于支撑阻力强度排序）
    tf_strength = {
        "monthly": 6,
        "weekly": 5,
        "daily": 4,
        "4h": 3,
        "2h": 2,
        "1h": 1
    }
    
    for tf_name, pivots in all_pivots_by_tf.items():
        strength = tf_strength.get(tf_name, 1)
        for p in pivots:
            zg = p.get("zg", 0)
            zd = p.get("zd", 0)
            # 支撑位：中枢下沿低于当前价格
            if zd < current_price:
                supports.append({"price": zd, "strength": strength, "tf": tf_name})
            # 阻力位：中枢上沿高于当前价格
            if zg > current_price:
                resistances.append({"price": zg, "strength": strength, "tf": tf_name})
    
    # 按强度排序，取最近的强支撑/阻力
    supports.sort(key=lambda x: (-x["strength"], -x["price"]))
    resistances.sort(key=lambda x: (-x["strength"], x["price"]))
    
    # 返回最近的支撑阻力（考虑强度）
    nearest_support = supports[0]["price"] if supports else None
    nearest_resistance = resistances[0]["price"] if resistances else None
    
    return nearest_support, nearest_resistance


def calc_prediction(analysis_result, current_price):
    """基于缠论分析计算各时间级别预测点位（每个级别独立计算）"""
    if not analysis_result or not current_price:
        return {}
    
    predictions = {}
    
    # 各时间级别独立预测（波动率递增）
    tf_configs = [
        ("1h", "1小时", 0.003),     # 1小时波动~0.3%
        ("2h", "2小时", 0.005),     # 2小时波动~0.5%
        ("4h", "4小时", 0.008),     # 4小时波动~0.8%
        ("daily", "日线", 0.025),   # 日线波动~2.5%
        ("weekly", "周线", 0.06),   # 周线波动~6%
        ("monthly", "月线", 0.12)   # 月线波动~12%
    ]
    
    timeframes = analysis_result.get("timeframes", {})
    if not timeframes:
        return predictions
    
    for pred_key, pred_name, volatility in tf_configs:
        tf_result = timeframes.get(pred_key)
        if not tf_result:
            continue
        
        signal = tf_result.get("signal", "●观望")
        trend = tf_result.get("trend", "unknown")
        
        # 该级别自身的支撑阻力
        tf_support = tf_result.get("nearest_support", 0)
        tf_resistance = tf_result.get("nearest_resistance", 0)
        
        # 计算预测分数
        score = 0
        
        # 信号权重（与calc_position_advice保持一致）
        signal_scores = {
            "★一买": 3, "★二买": 2.5, "★一买弱": 2, "★抢跑多": 1.8, "★二买区": 1.5, "◆三买": 1.3, "●抢跑多": 1.2,
            "▲一卖": -3, "▲二卖": -2.5, "▲一卖弱": -2, "★抢跑空": -1.8, "◆三卖": -1.5, "●抢跑空": -1.2,
            "★强多": 2.8, "★偏多": 1.5, "▲强空": -2.8, "▲偏空": -1.5,
            "●偏多": 0.8, "●偏空": -0.8, "●观望": 0
        }
        score += signal_scores.get(signal, 0)
        
        # 趋势权重
        trend_scores = {"up": 1.5, "down": -1.5, "consolidation": 0, "unknown": 0}
        score += trend_scores.get(trend, 0)
        
        # 该级别支撑阻力距离
        if tf_support and tf_resistance:
            dist_to_support = (current_price - tf_support) / current_price
            dist_to_resistance = (tf_resistance - current_price) / current_price
            if dist_to_support < 0.02:  # 靠近支撑 = 看涨
                score += 1
            if dist_to_resistance < 0.02:  # 靠近阻力 = 看跌
                score -= 1
        
        # 主力意图权重（该级别自身的意图）
        mm_intentions = tf_result.get("mm_intention", [])
        if mm_intentions and isinstance(mm_intentions, list) and len(mm_intentions) > 0:
            mm_first = mm_intentions[0]
            if isinstance(mm_first, dict):
                mm_type = mm_first.get("type", "")
                mm_conf = mm_first.get("confidence", 0)
                mm_weight = mm_conf * 1.5  # 意图权重（最大1.5）
                
                # 多头意图加分
                if mm_type in ["吸筹", "逼空", "拉升", "护盘", "震荡洗盘", "利空不跌",
                               "向上试盘", "低开大阳试盘"]:
                    score += mm_weight
                # 空头意图减分
                elif mm_type in ["出货", "诱多", "打压", "放量滞涨", "缩量上涨", "放量下跌", "利好不涨",
                                 "向下试盘", "高开低走试盘"]:
                    score -= mm_weight
                # 自然走势、地量地价不加分
        
        # 计算目标点位
        if score > 0:
            expected_pct = min(volatility * 2, volatility * score * 0.5)
            target = current_price * (1 + expected_pct)
            stop_loss = current_price * (1 - volatility * 0.5)
            direction = "↑看涨"
        elif score < 0:
            expected_pct = min(volatility * 2, volatility * abs(score) * 0.5)
            target = current_price * (1 - expected_pct)
            stop_loss = current_price * (1 + volatility * 0.5)
            direction = "↓看跌"
        else:
            target = current_price
            stop_loss = current_price * (1 + volatility * 0.5) if current_price > 0 else 0
            direction = "↔震荡"
        
        predictions[pred_key] = {
            "name": pred_name,
            "direction": direction,
            "target": round(target, 2),
            "stop_loss": round(stop_loss, 2),
            "score": round(score, 2)
        }
    
    return predictions


def analyze_commodity_groups(all_analysis):
    """多品种联立分析：黄金、白银、原油组内现货与股票信号联动
    
    抢跑检测：
    - 股票抢跑：股票先于现货出现买卖点
    - 内盘抢跑：国内期货先于国际现货出现信号
    - 白银抢跑：白银先于黄金出现信号（白银波动大，常领先黄金转向）
    
    返回每个品种组的综合信号和联动状态
    """
    group_results = {}
    
    # 信号分值映射（缠论买卖点强度层级）
    signal_score_map = {
        "★一买": 3, "★二买": 2.5, "★一买弱": 2, "★抢跑多": 1.8, "★二买区": 1.5, "◆三买": 1.3, "●抢跑多": 1.2,
        "▲一卖": -3, "▲二卖": -2.5, "▲一卖弱": -2, "★抢跑空": -1.8, "◆三卖": -1.5, "●抢跑空": -1.2,
        "★强多": 2.8, "★偏多": 1.5, "▲强空": -2.8, "▲偏空": -1.5,
        "●偏多": 0.8, "●偏空": -0.8, "●观望": 0
    }
    
    # 第一遍：计算各组基础分数
    for group_name, group_cfg in COMMODITY_GROUPS.items():
        spot_code = group_cfg["spot"]
        domestic_code = group_cfg.get("domestic", "")
        stock_codes = group_cfg["stocks"]
        color = group_cfg["color"]
        
        # 获取国际现货信号
        spot_analysis = all_analysis.get(spot_code, {})
        spot_signal = spot_analysis.get("signal", "●观望")
        spot_score_val = signal_score_map.get(spot_signal, 0)
        
        # 获取国内期货信号
        domestic_score_val = 0
        if domestic_code:
            domestic_ana = all_analysis.get(domestic_code, {})
            domestic_signal = domestic_ana.get("signal", "●观望")
            domestic_score_val = signal_score_map.get(domestic_signal, 0)
        
        # 获取股票信号
        stock_scores = []
        stock_signals = []
        stock_buy_count = 0
        stock_sell_count = 0
        for code in stock_codes:
            stock_ana = all_analysis.get(code, {})
            stock_signal = stock_ana.get("signal", "●观望")
            stock_score = signal_score_map.get(stock_signal, 0)
            stock_scores.append(stock_score)
            stock_signals.append(stock_signal)
            if "买" in stock_signal:
                stock_buy_count += 1
            if "卖" in stock_signal:
                stock_sell_count += 1
        
        # 计算股票平均信号
        avg_stock_score = sum(stock_scores) / len(stock_scores) if stock_scores else 0
        
        # ---- 抢跑检测 ----
        # 股票抢跑做多：股票出现买点但现货尚未确认
        stock_leading_buy = (avg_stock_score >= 1.0 and spot_score_val < 0.5)
        # 股票抢跑做空：股票出现卖点但现货尚未确认
        stock_leading_sell = (avg_stock_score <= -1.0 and spot_score_val > -0.5)
        # 国内期货领先于国际现货
        domestic_leading_buy = (domestic_score_val >= 1.0 and spot_score_val < 0.5)
        domestic_leading_sell = (domestic_score_val <= -1.0 and spot_score_val > -0.5)
        
        # ---- 动态权重调整 ----
        # 默认：国际现货40%，国内期货30%，股票30%
        # 国内期货抢跑时：国际现货30%，国内期货40%，股票30%
        # 股票抢跑时：国际现货30%，国内期货20%，股票50%
        if stock_leading_buy or stock_leading_sell:
            spot_weight, domestic_weight, stock_weight = 0.3, 0.2, 0.5
        elif domestic_leading_buy or domestic_leading_sell:
            spot_weight, domestic_weight, stock_weight = 0.3, 0.4, 0.3
        else:
            spot_weight, domestic_weight, stock_weight = 0.4, 0.3, 0.3
        
        total_score = (spot_score_val * spot_weight + 
                      domestic_score_val * domestic_weight + 
                      avg_stock_score * stock_weight)
        
        # ---- 判断联动状态（第一遍：股票和内盘抢跑）----
        if stock_leading_buy:
            linkage = "股票抢跑做多"
            linkage_color = "#FF4444"
        elif stock_leading_sell:
            linkage = "股票抢跑做空"
            linkage_color = "#4CAF50"
        elif domestic_leading_buy:
            linkage = "内盘抢跑做多"
            linkage_color = "#FF6B35"
        elif domestic_leading_sell:
            linkage = "内盘抢跑做空"
            linkage_color = "#2196F3"
        elif spot_score_val > 0.5 and avg_stock_score > 0.5:
            linkage = "共振做多"
            linkage_color = "#FF4444"
        elif spot_score_val < -0.5 and avg_stock_score < -0.5:
            linkage = "共振做空"
            linkage_color = "#4CAF50"
        elif spot_score_val > 0.5 and avg_stock_score < -0.5:
            linkage = "股弱现强"
            linkage_color = "#FFA500"
        elif spot_score_val < -0.5 and avg_stock_score > 0.5:
            linkage = "股强现弱"
            linkage_color = "#FFA500"
        else:
            linkage = "震荡观望"
            linkage_color = "#888888"
        
        # ---- 确定综合信号（考虑抢跑） ----
        if stock_leading_buy:
            # 股票抢跑做多，综合信号提前反映多头
            if total_score >= 1.5:
                main_signal = "★抢跑多"
            else:
                main_signal = "●抢跑多"
        elif stock_leading_sell:
            # 股票抢跑做空，综合信号提前反映空头
            if total_score <= -1.5:
                main_signal = "▲抢跑空"
            else:
                main_signal = "●抢跑空"
        elif total_score >= 2.0:
            main_signal = "★强多"
        elif total_score >= 0.8:
            main_signal = "★偏多"
        elif total_score <= -2.0:
            main_signal = "▲强空"
        elif total_score <= -0.8:
            main_signal = "▲偏空"
        elif total_score > 0:
            main_signal = "●偏多"
        elif total_score < 0:
            main_signal = "●偏空"
        else:
            main_signal = "●观望"
        
        group_results[group_name] = {
            "signal": main_signal,
            "score": round(total_score, 2),
            "linkage": linkage,
            "linkage_color": linkage_color,
            "spot_signal": spot_signal,
            "spot_score": spot_score_val,
            "stock_avg_score": round(avg_stock_score, 2),
            "stock_signals": stock_signals,
            "stock_leading": stock_leading_buy or stock_leading_sell,
            "color": color,
            # 保存中间变量供第二遍使用
            "_stock_leading_buy": stock_leading_buy,
            "_stock_leading_sell": stock_leading_sell,
            "_domestic_leading_buy": domestic_leading_buy,
            "_domestic_leading_sell": domestic_leading_sell,
        }
    
    # 第二遍：检测白银抢跑黄金（跨品种抢跑）
    if "黄金" in group_results and "白银" in group_results:
        gold = group_results["黄金"]
        silver = group_results["白银"]
        silver_score = silver["spot_score"]
        gold_score = gold["spot_score"]
        
        # 白银抢跑黄金
        silver_leading_buy = (silver_score >= 1.0 and gold_score < 0.5)
        silver_leading_sell = (silver_score <= -1.0 and gold_score > -0.5)
        
        if silver_leading_buy or silver_leading_sell:
            # 更新黄金组的联动状态
            if silver_leading_buy:
                gold["linkage"] = "白银抢跑做多"
                gold["linkage_color"] = "#FFD700"
            else:
                gold["linkage"] = "白银抢跑做空"
                gold["linkage_color"] = "#C0C0C0"
            # 更新信号
            if silver_leading_buy:
                gold["signal"] = "★抢跑多" if gold["score"] >= 1.0 else "●抢跑多"
            else:
                gold["signal"] = "▲抢跑空" if gold["score"] <= -1.0 else "●抢跑空"
    
    # 清理中间变量
    for g in group_results.values():
        g.pop("_stock_leading_buy", None)
        g.pop("_stock_leading_sell", None)
        g.pop("_domestic_leading_buy", None)
        g.pop("_domestic_leading_sell", None)
    
    return group_results


def aggregate_klines_60m(klines_60m, hours):
    """将60分钟K线聚合为更高时间级别 (1h/2h/4h)
    hours: 1=1小时, 2=2小时, 4=4小时
    """
    if not klines_60m or hours <= 1:
        return klines_60m
    
    result = []
    i = 0
    while i < len(klines_60m):
        # 取hours根K线合并
        chunk = klines_60m[i:i+hours]
        if not chunk:
            break
        # 合并规则: 第一根open, 最大high, 最小low, 最后一根close
        # 处理volume可能是字符串的情况
        total_vol = 0
        for c in chunk:
            v = c.get("volume", 0)
            if isinstance(v, str):
                try:
                    v = float(v)
                except:
                    v = 0
            total_vol += v
        
        merged = {
            "day": chunk[-1]["day"],  # 用最后一根的时间
            "open": chunk[0]["open"],
            "high": max(c["high"] for c in chunk),
            "low": min(c["low"] for c in chunk),
            "close": chunk[-1]["close"],
            "volume": total_vol
        }
        result.append(merged)
        i += hours
    return result


def aggregate_daily_to_monthly(klines_daily):
    """将日K线聚合为月K线"""
    if not klines_daily:
        return None
    
    # 按月分组
    months = {}
    for k in klines_daily:
        # day格式: "2026-08-07" 或 "2026-08-07 14:00:00"
        day_str = k["day"][:10]
        month_key = day_str[:7]  # "2026-08"
        if month_key not in months:
            months[month_key] = []
        months[month_key].append(k)
    
    # 每月合并
    result = []
    for month_key in sorted(months.keys()):
        chunk = months[month_key]
        # 处理volume可能是字符串的情况
        total_vol = 0
        for c in chunk:
            v = c.get("volume", 0)
            if isinstance(v, str):
                try:
                    v = float(v)
                except:
                    v = 0
            total_vol += v
        
        merged = {
            "day": chunk[-1]["day"],  # 用最后一天的日期
            "open": chunk[0]["open"],
            "high": max(c["high"] for c in chunk),
            "low": min(c["low"] for c in chunk),
            "close": chunk[-1]["close"],
            "volume": total_vol
        }
        result.append(merged)
    return result


def aggregate_daily_to_weekly(klines_daily):
    """将日K线聚合为周K线"""
    if not klines_daily:
        return None
    
    from datetime import datetime
    
    # 按周分组
    weeks = {}
    for k in klines_daily:
        day_str = k["day"][:10]
        try:
            dt = datetime.strptime(day_str, "%Y-%m-%d")
            # 使用ISO周：(年份, 周数)
            week_key = f"{dt.isocalendar()[0]}-W{dt.isocalendar()[1]:02d}"
            if week_key not in weeks:
                weeks[week_key] = []
            weeks[week_key].append(k)
        except:
            continue
    
    # 每周合并
    result = []
    for week_key in sorted(weeks.keys()):
        chunk = weeks[week_key]
        total_vol = 0
        for c in chunk:
            v = c.get("volume", 0)
            if isinstance(v, str):
                try:
                    v = float(v)
                except:
                    v = 0
            total_vol += v
        
        merged = {
            "day": chunk[-1]["day"],  # 用最后一天的日期
            "open": chunk[0]["open"],
            "high": max(c["high"] for c in chunk),
            "low": min(c["low"] for c in chunk),
            "close": chunk[-1]["close"],
            "volume": total_vol
        }
        result.append(merged)
    return result


# ============================================================
# 缠论核心算法引擎
# ============================================================

def _is_inclusive(a, b):
    """判断两根K线 a、b 是否存在包含关系（一根的高低区间完全覆盖另一根）"""
    return (a["high"] >= b["high"] and a["low"] <= b["low"]) or \
           (b["high"] >= a["high"] and b["low"] <= a["low"])


def merge_inclusive_klines(klines):
    """Step1: K线包含关系处理（缠论严格版）

    处理方向（向上/向下）由「合并对之前那根K线」与「合并对第一根」的关系决定：
      - 向上处理：取高高、低低（高点取二者高，低点也取二者高）
      - 向下处理：取低高、低低（高点取二者低，低点取二者低）
    这样可保证合并后K线的高低点不破坏原趋势方向的极值，是分型/笔识别的基础。
    """
    if len(klines) < 2:
        return [dict(k) for k in klines]
    merged = [dict(klines[0])]
    direction = 0  # 0=尚未确定, 1=向上, -1=向下
    for i in range(1, len(klines)):
        cur = klines[i]
        last = merged[-1]
        if not _is_inclusive(last, cur):
            # 非包含：确定初始方向（首次出现两根非包含K线时）
            if direction == 0:
                direction = 1 if cur["high"] > last["high"] else -1
            merged.append(dict(cur))
        else:
            # 包含：方向由「last 之前的合并K线」与 last 的关系决定
            if len(merged) >= 2:
                before = merged[-2]
                up = (before["high"] < last["high"]) or (before["low"] < last["low"])
                direction = 1 if up else -1
            else:
                # 仅有1根历史，用 cur 与 last 的关系作为初始方向
                direction = 1 if cur["high"] > last["high"] else -1
            if direction >= 0:  # 向上处理：高高、低低
                merged[-1] = {**last, "high": max(last["high"], cur["high"]),
                              "low": max(last["low"], cur["low"])}
            else:               # 向下处理：低高、低低
                merged[-1] = {**last, "high": min(last["high"], cur["high"]),
                              "low": min(last["low"], cur["low"])}
    return merged


def find_fractals(klines):
    """Step2: 识别顶底分型
    顶分型: 中间K线的高点 > 左右两根的高点
    底分型: 中间K线的低点 < 左右两根的低点
    """
    fractals = []
    for i in range(1, len(klines) - 1):
        prev_k, cur_k, next_k = klines[i-1], klines[i], klines[i+1]
        if cur_k["high"] > prev_k["high"] and cur_k["high"] > next_k["high"]:
            fractals.append({"type": "top", "index": i, "value": cur_k["high"],
                             "day": cur_k.get("day", "")})
        elif cur_k["low"] < prev_k["low"] and cur_k["low"] < next_k["low"]:
            fractals.append({"type": "bottom", "index": i, "value": cur_k["low"],
                             "day": cur_k.get("day", "")})
    return fractals


def build_strokes(fractals, klines):
    """Step3: 构建笔（缠论标准定义）

    笔是缠论的最基础构件，定义严格且宽松：
      1. 顶底分型必须交替出现，同类型相邻分型只保留更极值者；
      2. 相邻异型分型之间至少存在 1 根独立K线（合并K线索引差 >= 2）；
      3. 同向更极值分型（更高高 / 更低低）动态延伸当前未确认笔的端点，
         避免被中途小回撤截断，保证笔序列严格交替。
    注：笔不要求「突破前同向极值」——那属于线段（更高层级）的约束，
        放在笔上会错误丢弃下跌趋势中的更低反弹高点。
    """
    if not fractals:
        return []
    # 1) 交替 + 同型取极值
    cleaned = [fractals[0]]
    for f in fractals[1:]:
        if f["type"] == cleaned[-1]["type"]:
            if (f["type"] == "top" and f["value"] > cleaned[-1]["value"]) or \
               (f["type"] == "bottom" and f["value"] < cleaned[-1]["value"]):
                cleaned[-1] = f
        else:
            cleaned.append(f)

    # 2) 波浪推进：顶底交替成笔 + 动态延伸
    strokes = []
    pending = cleaned[0]
    for f in cleaned[1:]:
        if f["type"] == pending["type"]:
            # 同向且更极值：延伸当前未确认笔（更新起点为该更极值分型）
            if (f["type"] == "top" and f["value"] > pending["value"]) or \
               (f["type"] == "bottom" and f["value"] < pending["value"]):
                pending = f
                if strokes:
                    sdir = "up" if f["type"] == "top" else "down"
                    same = next((s for s in reversed(strokes) if s["direction"] == sdir), None)
                    if same is not None:
                        same["high"] = max(same["high"], f["value"])
                        same["low"] = min(same["low"], f["value"])
                        same["end"] = f
            continue

        direction = "up" if f["type"] == "top" else "down"
        # 至少 1 根独立K线（合并K线索引差 >= 2）方成笔
        if f["index"] - pending["index"] >= 2:
            strokes.append({"start": pending, "end": f,
                            "high": max(pending["value"], f["value"]),
                            "low": min(pending["value"], f["value"]),
                            "direction": direction})
            pending = f
        # 间隔不足：忽略该分型，pending 不变（等待后续分型）
    return strokes


def find_pivots(strokes):
    """Step4: 识别笔中枢（缠论标准定义）

    至少连续 3 笔的重叠区域构成中枢：
      ZG = min(各笔高点)，ZD = max(各笔低点) —— 中枢震荡区间（中枢实体）
      GG = max(各笔高点)，DD = min(各笔低点) —— 中枢波动极限（中枢引力边界）
    有效条件: ZG > ZD。中枢方向由进入笔与离开笔的相对高低推断。
    """
    pivots = []
    if len(strokes) < 3:
        return pivots
    i = 0
    while i <= len(strokes) - 3:
        s1, s2, s3 = strokes[i], strokes[i+1], strokes[i+2]
        zg = min(s1["high"], s2["high"], s3["high"])
        zd = max(s1["low"], s2["low"], s3["low"])
        if zg > zd:
            # 找到中枢，尝试向后扩展
            pivot_strokes = [s1, s2, s3]
            j = i + 3
            while j < len(strokes):
                sj = strokes[j]
                new_zg = min(zg, sj["high"])
                new_zd = max(zd, sj["low"])
                if new_zg > new_zd:
                    pivot_strokes.append(sj)
                    zg, zd = new_zg, new_zd
                    j += 1
                else:
                    break
            gg = max(s["high"] for s in pivot_strokes)
            dd = min(s["low"] for s in pivot_strokes)
            # 中枢方向：比较中枢前最后一根笔与中枢后第一根笔（若有）的趋势
            enter = pivot_strokes[0]
            direction = "up" if enter["direction"] == "up" else "down"
            pivots.append({
                "zg": zg, "zd": zd, "gg": gg, "dd": dd,
                "start_day": pivot_strokes[0]["start"].get("day", ""),
                "end_day": pivot_strokes[-1]["end"].get("day", ""),
                "stroke_count": len(pivot_strokes),
                "direction": direction,
                "start_idx": i, "end_idx": j - 1,
                # 合并K线坐标（用于K线图水平定位），闭区间
                "x0": pivot_strokes[0]["start"]["index"],
                "x1": pivot_strokes[-1]["end"]["index"],
            })
            i = j
        else:
            i += 1
    return pivots


def build_segments(strokes):
    """Step4.5: 由笔聚合出线段（缠论核心层级：分型→笔→线段→中枢）

    线段是比笔更高一级的同向走势：由至少 3 笔构成，其内部高低点呈同向推进；
    当反向笔跌破（上涨线段）/升破（下跌线段）线段起点的极值时，该线段结束，
    新的反向线段开始。线段是「标准中枢」与「趋势/盘整」判定的基础。
    """
    if len(strokes) < 3:
        return []

    def _make_seg(strokes_list, direction):
        highs = [s["high"] for s in strokes_list]
        lows = [s["low"] for s in strokes_list]
        return {
            "direction": direction,
            "strokes": strokes_list,
            "high": max(highs), "low": min(lows),
            "start_idx": strokes_list[0]["start"]["index"],
            "end_idx": strokes_list[-1]["end"]["index"],
            "start_day": strokes_list[0]["start"].get("day", ""),
            "end_day": strokes_list[-1]["end"].get("day", ""),
            "stroke_count": len(strokes_list),
        }

    segs = []
    seg_dir = strokes[0]["direction"]
    seg_start = strokes[0]
    seg_strokes = [strokes[0]]
    for s in strokes[1:]:
        if s["direction"] == seg_dir:
            seg_strokes.append(s)
        else:
            # 反向笔：判断是否终结当前线段
            if seg_dir == "up" and s["low"] < seg_start["low"]:
                if len(seg_strokes) >= 3:
                    segs.append(_make_seg(seg_strokes, seg_dir))
                seg_dir = "down"; seg_start = s; seg_strokes = [s]
            elif seg_dir == "down" and s["high"] > seg_start["high"]:
                if len(seg_strokes) >= 3:
                    segs.append(_make_seg(seg_strokes, seg_dir))
                seg_dir = "up"; seg_start = s; seg_strokes = [s]
            else:
                # 未破线段起点极值 → 视为线段内回撤，线段延续
                seg_strokes.append(s)
    if len(seg_strokes) >= 3:
        segs.append(_make_seg(seg_strokes, seg_dir))
    return segs


def build_pivots_from_segments(segments):
    """由线段构建标准中枢（线段中枢）

    至少连续 3 个线段的重叠区域构成标准中枢；其 ZG/ZD 为各线段高低重叠区，
    GG/DD 为线段波动极限。标准中枢比笔中枢更稳定，是背驰与买卖点的可靠基准。
    """
    pivots = []
    if len(segments) < 3:
        return pivots
    i = 0
    while i <= len(segments) - 3:
        g1, g2, g3 = segments[i], segments[i+1], segments[i+2]
        zg = min(g1["high"], g2["high"], g3["high"])
        zd = max(g1["low"], g2["low"], g3["low"])
        if zg > zd:
            pivot_segs = [g1, g2, g3]
            j = i + 3
            while j < len(segments):
                gj = segments[j]
                new_zg = min(zg, gj["high"])
                new_zd = max(zd, gj["low"])
                if new_zg > new_zd:
                    pivot_segs.append(gj)
                    zg, zd = new_zg, new_zd
                    j += 1
                else:
                    break
            gg = max(g["high"] for g in pivot_segs)
            dd = min(g["low"] for g in pivot_segs)
            direction = "up" if pivot_segs[0]["direction"] == "up" else "down"
            pivots.append({
                "zg": zg, "zd": zd, "gg": gg, "dd": dd,
                "start_day": pivot_segs[0]["start_day"],
                "end_day": pivot_segs[-1]["end_day"],
                "segment_count": len(pivot_segs),
                "direction": direction,
                "start_idx": i, "end_idx": j - 1,
                "from_segments": True,
                # 合并K线坐标（用于K线图水平定位），闭区间
                "x0": pivot_segs[0]["start_idx"],
                "x1": pivot_segs[-1]["end_idx"],
            })
            i = j
        else:
            i += 1
    return pivots


def calc_macd(closes, fast=12, slow=26, signal=9):
    """计算MACD指标"""
    if len(closes) < slow + signal:
        return [], [], []
    ema_fast = [closes[0]]
    ema_slow = [closes[0]]
    k_fast = 2.0 / (fast + 1)
    k_slow = 2.0 / (slow + 1)
    for i in range(1, len(closes)):
        ema_fast.append(closes[i] * k_fast + ema_fast[-1] * (1 - k_fast))
        ema_slow.append(closes[i] * k_slow + ema_slow[-1] * (1 - k_slow))
    dif = [ema_fast[i] - ema_slow[i] for i in range(len(closes))]
    dea = [dif[0]]
    k_sig = 2.0 / (signal + 1)
    for i in range(1, len(dif)):
        dea.append(dif[i] * k_sig + dea[-1] * (1 - k_sig))
    hist = [2 * (dif[i] - dea[i]) for i in range(len(dif))]
    return dif, dea, hist


def check_divergence(strokes, macd_hist, pivots=None):
    """Step5: 背驰检测（缠论严格版）

    比较最近两段同向笔的 MACD 红/绿柱面积：价格创新高/低但面积缩小 = 背驰。
    区分两类背驰：
      - 趋势背驰：出现在「趋势」末端（存在 ≥2 个同向不重叠中枢），力度最强，
        对应第一类买卖点；
      - 盘整背驰：出现在「盘整（单个中枢）」中，力度较弱，对应类一买/卖或中枢震荡结束。
    返回结构化结果，含面积比 ratio（用于区间套与信号强度量化）。

    返回: {"type": "top"/"bottom"/"none", "kind": "trend"/"consolidation"/"none",
           "ratio": float, "last_area": float, "prev_area": float}
    """
    result = {"type": "none", "kind": "none", "ratio": 1.0, "last_area": 0.0, "prev_area": 0.0}
    if len(strokes) < 4 or not macd_hist:
        return result
    # 取最近两段同向笔
    last_stroke = strokes[-1]
    prev_same_dir = None
    for s in reversed(strokes[:-1]):
        if s["direction"] == last_stroke["direction"]:
            prev_same_dir = s
            break
    if not prev_same_dir:
        return result

    def get_macd_sum(stroke):
        s_idx = stroke["start"]["index"]
        e_idx = stroke["end"]["index"]
        if s_idx >= len(macd_hist) or e_idx >= len(macd_hist) or e_idx < s_idx:
            return 0.0
        return sum(abs(macd_hist[i]) for i in range(s_idx, e_idx + 1))

    last_area = get_macd_sum(last_stroke)
    prev_area = get_macd_sum(prev_same_dir)
    if prev_area <= 0:
        return result
    ratio = last_area / prev_area
    div_type = None
    if last_stroke["direction"] == "up":
        if last_stroke["high"] >= prev_same_dir["high"] and ratio < 0.8:
            div_type = "top"      # 顶背驰
    else:
        if last_stroke["low"] <= prev_same_dir["low"] and ratio < 0.8:
            div_type = "bottom"   # 底背驰
    if div_type is None:
        return result

    # 判断是否趋势背驰
    kind = "consolidation"
    if pivots:
        if div_type == "bottom":
            down_trend, _, _ = is_valid_trend(pivots, "down")
            if down_trend and last_stroke["low"] <= min(p["zd"] for p in pivots):
                kind = "trend"
        else:
            up_trend, _, _ = is_valid_trend(pivots, "up")
            if up_trend and last_stroke["high"] >= max(p["zg"] for p in pivots):
                kind = "trend"
    result.update({"type": div_type, "kind": kind, "ratio": ratio,
                   "last_area": last_area, "prev_area": prev_area})
    return result


def nested_interval_confirm(div_daily, div_smaller):
    """区间套确认：小级别与大级别在同一方向出现背驰时，买卖点精度大幅提升。

    返回: (is_nested: bool, level: str)
      is_nested=True 表示大小级别背驰共振（区间套成立）。
    """
    if not div_daily or div_daily.get("type") == "none" or not div_smaller:
        return False, ""
    t1, t2 = div_daily.get("type"), div_smaller.get("type")
    if t1 == t2 and t1 in ("top", "bottom"):
        return True, f"{'顶' if t1 == 'top' else '底'}背驰区间套"
    return False, ""


def is_valid_trend(pivots, direction="up"):
    """验证是否为有效趋势（缠论定义：至少2个不重叠中枢）
    
    上涨趋势：至少2个中枢，中枢依次升高（不重叠）
    下跌趋势：至少2个中枢，中枢依次降低（不重叠）
    
    返回: (is_trend, pivot_count, trend_strength)
    """
    if len(pivots) < 2:
        return False, len(pivots), 0
    
    # 检查中枢是否同向排列且不重叠
    valid_count = 1
    for i in range(1, len(pivots)):
        prev_pivot = pivots[i-1]
        curr_pivot = pivots[i]
        
        if direction == "up":
            # 上涨趋势：当前中枢下沿 > 前一中枢上沿（不重叠）
            if curr_pivot["zd"] > prev_pivot["zg"]:
                valid_count += 1
            elif curr_pivot["zd"] > prev_pivot["zd"]:
                # 部分重叠但重心升高
                valid_count += 0.5
        else:  # down
            # 下跌趋势：当前中枢上沿 < 前一中枢下沿（不重叠）
            if curr_pivot["zg"] < prev_pivot["zd"]:
                valid_count += 1
            elif curr_pivot["zg"] < prev_pivot["zg"]:
                # 部分重叠但重心降低
                valid_count += 0.5
    
    is_trend = valid_count >= 2
    strength = min(valid_count / 2, 1.0)  # 趋势强度 0-1
    return is_trend, len(pivots), strength


def calc_ma(closes, period):
    """计算移动平均线"""
    if len(closes) < period:
        return []
    ma = []
    for i in range(len(closes)):
        if i < period - 1:
            ma.append(None)
        else:
            ma.append(sum(closes[i-period+1:i+1]) / period)
    return ma


def detect_ma_kiss(closes, short_period=5, long_period=20):
    """检测均线"吻"形态（缠论第15-20章）
    
    飞吻：短期均线略走平后继续原趋势（趋势强烈）
    唇吻：短期均线靠近长期均线但不跌破/升破（最常见）
    湿吻：短期均线跌破/升破长期均线并反复缠绕（转折信号）
    
    返回: {kiss_type, trend_direction, strength}
    """
    if len(closes) < long_period + 10:
        return {"type": "unknown", "desc": "数据不足"}
    
    ma_short = calc_ma(closes, short_period)
    ma_long = calc_ma(closes, long_period)
    
    # 取最近10根K线分析
    recent_short = [x for x in ma_short[-10:] if x is not None]
    recent_long = [x for x in ma_long[-10:] if x is not None]
    
    if len(recent_short) < 5 or len(recent_long) < 5:
        return {"type": "unknown", "desc": "数据不足"}
    
    # 计算短期均线与长期均线的距离变化
    distances = [recent_short[i] - recent_long[i] for i in range(min(len(recent_short), len(recent_long)))]
    
    # 当前趋势方向
    current_price = closes[-1]
    current_ma_long = ma_long[-1] if ma_long[-1] else current_price
    trend_up = current_price > current_ma_long
    
    # 检测交叉次数
    cross_count = 0
    for i in range(1, len(distances)):
        if distances[i] * distances[i-1] < 0:  # 符号变化 = 交叉
            cross_count += 1
    
    # 最小距离（相对于均线值）
    min_dist_ratio = min(abs(d) / current_ma_long for d in distances) if current_ma_long > 0 else 0
    
    # 判断吻类型
    if cross_count == 0:
        # 无交叉
        if min_dist_ratio < 0.005:  # 距离很近但不交叉
            return {
                "type": "唇吻",
                "desc": f"短期均线靠近长期均线但未跌破，{'上涨' if trend_up else '下跌'}中继",
                "trend": "up" if trend_up else "down",
                "strength": 0.7
            }
        else:
            return {
                "type": "飞吻",
                "desc": f"短期均线略走平后继续{'上涨' if trend_up else '下跌'}，趋势强烈",
                "trend": "up" if trend_up else "down",
                "strength": 0.9
            }
    elif cross_count <= 2:
        # 1-2次交叉 = 湿吻（转折信号）
        return {
            "type": "湿吻",
            "desc": f"短期均线反复穿越长期均线，可能出现转折",
            "trend": "转折" if cross_count == 2 else ("up" if trend_up else "down"),
            "strength": 0.5
        }
    else:
        # 多次交叉 = 强湿吻（强烈转折信号）
        return {
            "type": "强湿吻",
            "desc": "均线反复缠绕，趋势即将发生重大转折",
            "trend": "转折",
            "strength": 0.3
        }


def analyze_divergence_reversal(pivots, divergence_type, current_price):
    """分析背驰后的三种演化方向（缠论背驰-转折定理）
    
    三种情况：
    1. 级别扩展：最后一个中枢扩展为更大级别中枢（最弱反弹）
    2. 更大级别盘整：形成比原趋势中枢级别更大的盘整（中等）
    3. 反趋势：形成与原趋势反向的新趋势（最强）
    
    返回: {reversal_type, probability, description}
    """
    if len(pivots) < 2:
        return {"type": "unknown", "prob": 0, "desc": "中枢不足，无法判断"}
    
    last_pivot = pivots[-1]
    prev_pivot = pivots[-2]
    
    # 计算最后一个中枢的位置和大小
    last_pivot_center = (last_pivot["zg"] + last_pivot["zd"]) / 2
    last_pivot_size = last_pivot["zg"] - last_pivot["zd"]
    
    # 价格相对于最后中枢的位置
    if divergence_type == "bottom_divergence":
        # 底背驰后的分析
        dist_to_pivot = (last_pivot["zd"] - current_price) / last_pivot["zd"] if last_pivot["zd"] > 0 else 0
        
        if dist_to_pivot > 0.05:  # 价格远离中枢下方
            # 更可能形成反趋势
            return {
                "type": "反趋势",
                "prob": 0.6,
                "desc": f"价格远离中枢下方{dist_to_pivot*100:.1f}%，可能形成反趋势上涨"
            }
        elif dist_to_pivot > 0.02:
            # 可能形成更大级别盘整
            return {
                "type": "更大级别盘整",
                "prob": 0.5,
                "desc": "价格在中枢下方附近，可能形成更大级别盘整"
            }
        else:
            # 可能只是级别扩展
            return {
                "type": "级别扩展",
                "prob": 0.7,
                "desc": "价格接近中枢，最后一个中枢可能扩展"
            }
    else:
        # 顶背驰后的分析
        dist_to_pivot = (current_price - last_pivot["zg"]) / last_pivot["zg"] if last_pivot["zg"] > 0 else 0
        
        if dist_to_pivot > 0.05:
            return {
                "type": "反趋势",
                "prob": 0.6,
                "desc": f"价格远离中枢上方{dist_to_pivot*100:.1f}%，可能形成反趋势下跌"
            }
        elif dist_to_pivot > 0.02:
            return {
                "type": "更大级别盘整",
                "prob": 0.5,
                "desc": "价格在中枢上方附近，可能形成更大级别盘整"
            }
        else:
            return {
                "type": "级别扩展",
                "prob": 0.7,
                "desc": "价格接近中枢，最后一个中枢可能扩展"
            }


# ============================================================
# 主力意图识别
# ============================================================

def detect_mm_intention(klines, strokes, pivots, macd_hist, divergence, trend):
    """识别主力意图：震荡洗盘、吸筹、诱多、逼空
    
    综合K线形态、量价关系、中枢位置、MACD行为来判断主力可能的操作意图。
    返回: [{"type": str, "confidence": float, "desc": str, "color": str}, ...]
    """
    if not klines or len(klines) < 15 or not strokes:
        return []
    
    intentions = []
    current_price = klines[-1]["close"]
    closes = [k["close"] for k in klines]
    
    # 提取成交量序列
    volumes = []
    for k in klines:
        v = k.get("volume", 0)
        if isinstance(v, str):
            try:
                v = float(v)
            except:
                v = 0
        volumes.append(v)
    
    # 检查成交量数据是否有效（现货黄金/白银等品种成交量可能为0）
    has_volume = sum(volumes) > 0
    
    last_pivot = pivots[-1] if pivots else None
    last_stroke = strokes[-1] if strokes else None
    prev_stroke = strokes[-2] if len(strokes) >= 2 else None
    
    # ---- 辅助指标计算 ----
    # 近10根K线平均成交量
    recent_vol_avg = sum(volumes[-10:]) / min(len(volumes), 10) if volumes else 0
    # 前10根K线平均成交量
    prev_vol_avg = sum(volumes[-20:-10]) / min(len(volumes[:-10]), 10) if len(volumes) > 10 else recent_vol_avg
    vol_ratio = recent_vol_avg / prev_vol_avg if prev_vol_avg > 0 else 1
    
    # 最近笔的成交量变化
    def stroke_vol_ratio(stroke):
        s_idx = stroke["start"]["index"]
        e_idx = stroke["end"]["index"]
        if s_idx >= len(volumes) or e_idx >= len(volumes):
            return 0
        stroke_vols = volumes[s_idx:e_idx+1]
        return sum(stroke_vols) / len(stroke_vols) if stroke_vols else 0
    
    # 最近K线的上影线/下影线比例
    def shadow_ratio(k, direction="lower"):
        body = abs(k["close"] - k["open"])
        total_range = k["high"] - k["low"]
        if total_range <= 0:
            return 0
        if direction == "lower":
            shadow = min(k["open"], k["close"]) - k["low"]
        else:
            shadow = k["high"] - max(k["open"], k["close"])
        return shadow / total_range
    
    # 最近5根K线的平均下影线/上影线比例
    recent_lower_shadows = [shadow_ratio(k, "lower") for k in klines[-5:]]
    recent_upper_shadows = [shadow_ratio(k, "upper") for k in klines[-5:]]
    avg_lower_shadow = sum(recent_lower_shadows) / len(recent_lower_shadows) if recent_lower_shadows else 0
    avg_upper_shadow = sum(recent_upper_shadows) / len(recent_upper_shadows) if recent_upper_shadows else 0
    
    # MACD趋势（近5根柱状图变化）
    if macd_hist and len(macd_hist) >= 5:
        recent_hist = macd_hist[-5:]
        hist_trend = recent_hist[-1] - recent_hist[0]
        hist_shrinking_down = all(abs(recent_hist[i]) <= abs(recent_hist[i-1]) for i in range(1, len(recent_hist))) and recent_hist[-1] < 0
        hist_expanding_up = all(recent_hist[i] >= recent_hist[i-1] for i in range(1, len(recent_hist))) and recent_hist[-1] > 0
    else:
        hist_trend = 0
        hist_shrinking_down = False
        hist_expanding_up = False
    
    # ================================================================
    # 1. 震荡洗盘检测
    # 特征：价格跌破中枢下沿后快速收回，下跌缩量，长下影线
    # ================================================================
    shakeout_score = 0
    shakeout_desc = []
    
    if last_pivot and last_stroke:
        zg, zd = last_pivot["zg"], last_pivot["zd"]
        pivot_range = zg - zd
        
        # 条件1：最近一笔向下，且低点跌破中枢下沿
        if last_stroke["direction"] == "down":
            break_depth = zd - last_stroke["low"]
            break_depth_pct = break_depth / zd if zd > 0 else 0
            
            if break_depth > 0 and break_depth_pct < 0.03:  # 跌破但不深（<3%）
                shakeout_score += 2
                shakeout_desc.append(f"价格跌破中枢下沿{zd:.2f}后回升")
            elif break_depth > 0 and break_depth_pct < 0.05:  # 跌破3-5%
                shakeout_score += 1
                shakeout_desc.append(f"价格短暂跌破中枢下沿")
        
        # 条件2：价格已回到中枢内或上方
        if current_price >= zd:
            shakeout_score += 1.5
            shakeout_desc.append("价格已收回中枢区间")
        
        # 条件3：下跌过程缩量（仅当有成交量数据时）
        if has_volume and last_stroke["direction"] == "down" and prev_stroke and prev_stroke["direction"] == "up":
            down_vol = stroke_vol_ratio(last_stroke)
            up_vol = stroke_vol_ratio(prev_stroke)
            if up_vol > 0 and down_vol / up_vol < 0.7:
                shakeout_score += 2
                shakeout_desc.append("下跌明显缩量，抛压减弱")
            elif up_vol > 0 and down_vol / up_vol < 0.9:
                shakeout_score += 1
                shakeout_desc.append("下跌略有缩量")
        
        # 条件4：长下影线（锤子线）
        if avg_lower_shadow > 0.4:
            shakeout_score += 1.5
            shakeout_desc.append("近期频现长下影线，下方承接强")
        
        # 条件5：MACD下跌动能衰竭
        if hist_shrinking_down:
            shakeout_score += 1.5
            shakeout_desc.append("MACD绿柱逐步缩短，下跌动能衰竭")
        
        # 条件6：底背驰
        if divergence == "bottom_divergence":
            shakeout_score += 2
            shakeout_desc.append("出现底背驰信号")
    
    # 无成交量数据时降低阈值
    shakeout_threshold = 3 if not has_volume else 4
    if shakeout_score >= shakeout_threshold:
        confidence = min(shakeout_score / 8, 0.95)
        if not has_volume:
            shakeout_desc.insert(0, "无成交量数据，仅价格形态分析")
        intentions.append({
            "type": "震荡洗盘",
            "confidence": confidence,
            "desc": "；".join(shakeout_desc[:3]),
            "color": "#FF8C00",  # 深橙色
            "icon": "🔄"
        })
    
    # ================================================================
    # 2. 吸筹检测
    # 特征：中枢内反复震荡，成交量萎缩后逐步放大，价格重心抬升
    # ================================================================
    accum_score = 0
    accum_desc = []
    
    if last_pivot:
        zg, zd = last_pivot["zg"], last_pivot["zd"]
        pivot_range = zg - zd
        
        # 条件1：价格在中枢内部或略低于中枢
        if zd <= current_price <= zg:
            accum_score += 1
            accum_desc.append("价格在中枢区间内运行")
        elif current_price < zd and (zd - current_price) / zd < 0.02:
            accum_score += 0.5
            accum_desc.append("价格略低于中枢，试探支撑")
        
        # 条件2：中枢内笔数多（反复震荡）
        if last_pivot["stroke_count"] >= 5:
            accum_score += 2
            accum_desc.append(f"中枢内{last_pivot['stroke_count']}笔反复震荡")
        elif last_pivot["stroke_count"] >= 4:
            accum_score += 1
            accum_desc.append(f"中枢内{last_pivot['stroke_count']}笔震荡")
        
        # 条件3：成交量特征 - 整体缩量但近期有放大迹象（仅当有成交量数据时）
        if has_volume and recent_vol_avg > 0 and prev_vol_avg > 0:
            if vol_ratio < 0.8:
                # 整体缩量
                accum_score += 1
                accum_desc.append("成交量持续萎缩，浮筹减少")
            elif 0.8 <= vol_ratio <= 1.2:
                accum_score += 0.5
        
        # 条件4：价格重心抬升（中枢内低点逐步抬高）
        if len(strokes) >= 4:
            recent_down_strokes = [s for s in strokes[-6:] if s["direction"] == "down"]
            if len(recent_down_strokes) >= 2:
                lows = [s["low"] for s in recent_down_strokes]
                if all(lows[i] <= lows[i+1] for i in range(len(lows)-1)):
                    accum_score += 2.5
                    accum_desc.append("下跌笔低点依次抬高，重心上升")
                elif lows[-1] > lows[-2]:
                    accum_score += 1
                    accum_desc.append("最近低点抬升")
        
        # 条件5：MACD向零轴收敛
        if macd_hist and len(macd_hist) >= 3:
            last3 = macd_hist[-3:]
            if all(abs(last3[i]) < abs(last3[i-1]) + 0.001 for i in range(1, len(last3))):
                accum_score += 1
                accum_desc.append("MACD柱状图收敛，变盘在即")
        
        # 条件6：下跌趋势后进入中枢（前趋势为下跌）
        if trend == "consolidation" and len(pivots) >= 2:
            # 前一中枢在更低位置，说明从下跌进入盘整
            if pivots[-2]["zd"] < last_pivot["zd"]:
                accum_score += 1
                accum_desc.append("下跌后进入中枢盘整")
    
    # 无成交量数据时降低阈值
    accum_threshold = 3 if not has_volume else 4
    if accum_score >= accum_threshold:
        confidence = min(accum_score / 8, 0.95)
        if not has_volume:
            accum_desc.insert(0, "无成交量数据，仅价格形态分析")
        intentions.append({
            "type": "吸筹",
            "confidence": confidence,
            "desc": "；".join(accum_desc[:3]),
            "color": "#4169E1",  # 皇家蓝
            "icon": "📦"
        })
    
    # ================================================================
    # 3. 诱多检测
    # 特征：价格突破中枢上沿/前高后快速回落，上影线长，量价背离
    # ================================================================
    bull_trap_score = 0
    bull_trap_desc = []
    
    if last_pivot and last_stroke:
        zg, zd = last_pivot["zg"], last_pivot["zd"]
        
        # 条件1：最近一笔向上，且高点突破中枢上沿
        if last_stroke["direction"] == "up":
            break_height = last_stroke["high"] - zg
            break_pct = break_height / zg if zg > 0 else 0
            
            if break_pct > 0 and break_pct < 0.02:  # 突破但不远
                bull_trap_score += 1.5
                bull_trap_desc.append(f"价格短暂突破中枢上沿{zg:.2f}")
        
        # 条件2：价格已回落到中枢内或下方
        if current_price <= zg:
            bull_trap_score += 2
            bull_trap_desc.append("价格已回落至中枢区间内")
        if current_price < zd:
            bull_trap_score += 1
            bull_trap_desc.append("价格跌回中枢下方，突破失败")
        
        # 条件3：长上影线
        if avg_upper_shadow > 0.4:
            bull_trap_score += 2
            bull_trap_desc.append("近期频现长上影线，上方压力大")
        elif avg_upper_shadow > 0.3:
            bull_trap_score += 1
            bull_trap_desc.append("上方抛压显现")
        
        # 条件4：顶背驰
        if divergence == "top_divergence":
            bull_trap_score += 2.5
            bull_trap_desc.append("出现顶背驰，上涨动能不足")
        
        # 条件5：上涨笔缩量（量价背离，仅当有成交量数据时）
        if has_volume and last_stroke["direction"] == "up" and prev_stroke and prev_stroke["direction"] == "down":
            up_vol = stroke_vol_ratio(last_stroke)
            down_vol = stroke_vol_ratio(prev_stroke)
            if down_vol > 0 and up_vol / down_vol < 0.7:
                bull_trap_score += 2
                bull_trap_desc.append("上涨笔明显缩量，量价背离")
            elif down_vol > 0 and up_vol / down_vol < 0.9:
                bull_trap_score += 1
                bull_trap_desc.append("上涨量能不足")
        
        # 条件6：MACD红柱缩短
        if macd_hist and len(macd_hist) >= 3:
            last3 = macd_hist[-3:]
            if all(h > 0 for h in last3) and all(last3[i] <= last3[i-1] for i in range(1, len(last3))):
                bull_trap_score += 1.5
                bull_trap_desc.append("MACD红柱逐步缩短，多头力量衰竭")
    
    # 无成交量数据时降低阈值
    bull_trap_threshold = 3 if not has_volume else 4
    if bull_trap_score >= bull_trap_threshold:
        confidence = min(bull_trap_score / 8, 0.95)
        if not has_volume:
            bull_trap_desc.insert(0, "无成交量数据，仅价格形态分析")
        intentions.append({
            "type": "诱多",
            "confidence": confidence,
            "desc": "；".join(bull_trap_desc[:3]),
            "color": "#DC143C",  # 深红
            "icon": "🪤"
        })
    
    # ================================================================
    # 4. 逼空检测
    # 特征：强势突破中枢上沿，放量上涨，回调极浅，MACD强势扩张
    # ================================================================
    squeeze_score = 0
    squeeze_desc = []
    
    if last_pivot and last_stroke:
        zg, zd = last_pivot["zg"], last_pivot["zd"]
        pivot_range = zg - zd
        
        # 条件1：价格在中枢上方较远处
        if current_price > zg and pivot_range > 0:
            dist_pct = (current_price - zg) / pivot_range
            if dist_pct > 1.0:  # 距离超过中枢范围的100%
                squeeze_score += 2
                squeeze_desc.append(f"价格强势突破中枢上沿{zg:.2f}后继续上行")
            elif dist_pct > 0.5:
                squeeze_score += 1
                squeeze_desc.append(f"价格在中枢上方运行")
        
        # 条件2：最近上涨笔放量（仅当有成交量数据时）
        if has_volume and last_stroke["direction"] == "up":
            up_vol = stroke_vol_ratio(last_stroke)
            if prev_stroke:
                prev_vol = stroke_vol_ratio(prev_stroke)
                if prev_vol > 0 and up_vol / prev_vol > 1.3:
                    squeeze_score += 2.5
                    squeeze_desc.append("上涨笔明显放量，资金积极入场")
                elif prev_vol > 0 and up_vol / prev_vol > 1.1:
                    squeeze_score += 1.5
                    squeeze_desc.append("上涨笔温和放量")
        
        # 条件3：回调极浅（最后一笔向下很短就再次上涨）
        if len(strokes) >= 3:
            last_down = strokes[-1] if strokes[-1]["direction"] == "down" else (strokes[-2] if len(strokes) >= 2 and strokes[-2]["direction"] == "down" else None)
            if last_down and pivot_range > 0:
                pullback = last_down["high"] - last_down["low"]
                pullback_ratio = pullback / pivot_range
                if pullback_ratio < 0.3:  # 回调不到中枢范围的30%
                    squeeze_score += 2.5
                    squeeze_desc.append("回调极浅，空头无力反击")
                elif pullback_ratio < 0.5:
                    squeeze_score += 1
                    squeeze_desc.append("回调较浅")
        
        # 条件4：MACD红柱扩张
        if hist_expanding_up:
            squeeze_score += 2
            squeeze_desc.append("MACD红柱持续放大，多头动能强劲")
        
        # 条件5：连续上涨笔（至少2笔向上且高点抬升）
        if len(strokes) >= 4:
            recent_up = [s for s in strokes[-6:] if s["direction"] == "up"]
            if len(recent_up) >= 2:
                highs = [s["high"] for s in recent_up]
                if all(highs[i] < highs[i+1] for i in range(len(highs)-1)):
                    squeeze_score += 1.5
                    squeeze_desc.append("上涨笔高点依次抬升，攻势凌厉")
        
        # 条件6：成交量整体放大（仅当有成交量数据时）
        if has_volume and vol_ratio > 1.3:
            squeeze_score += 1
            squeeze_desc.append("近期成交量显著放大")
    
    # 无成交量数据时降低阈值
    squeeze_threshold = 3 if not has_volume else 4
    if squeeze_score >= squeeze_threshold:
        confidence = min(squeeze_score / 8, 0.95)
        if not has_volume:
            squeeze_desc.insert(0, "无成交量数据，仅价格形态分析")
        intentions.append({
            "type": "逼空",
            "confidence": confidence,
            "desc": "；".join(squeeze_desc[:3]),
            "color": "#FF1493",  # 深粉红
            "icon": "🚀"
        })
    
    # ================================================================
    # 5. 出货检测
    # 特征：高位滞涨，放量但价格不涨，上影线长，顶背驰
    # ================================================================
    dist_score = 0
    dist_desc = []
    
    if last_pivot and last_stroke:
        zg, zd = last_pivot["zg"], last_pivot["zd"]
        pivot_range = zg - zd
        
        # 条件1：价格在中枢上方或高位（距离中枢中心较远）
        pivot_center = (zg + zd) / 2
        if pivot_range > 0:
            height_ratio = (current_price - pivot_center) / pivot_range
            if height_ratio > 1.0:
                dist_score += 1.5
                dist_desc.append("价格处于高位，远离中枢中心")
            elif height_ratio > 0.5:
                dist_score += 0.5
        
        # 条件2：上涨趋势后进入高位盘整（至少2个中枢，中枢在高位）
        if trend == "up" or (len(pivots) >= 2 and pivots[-1]["zd"] > pivots[-2]["zg"]):
            dist_score += 1
            dist_desc.append("上涨趋势后处于高位")
        
        # 条件3：放量滞涨 - 成交量放大但价格波动收窄（仅当有成交量数据时）
        if has_volume and recent_vol_avg > 0 and prev_vol_avg > 0 and vol_ratio > 1.2:
            # 检查最近价格波动是否缩小
            recent_ranges = [k["high"] - k["low"] for k in klines[-10:]]
            prev_ranges = [k["high"] - k["low"] for k in klines[-20:-10]] if len(klines) >= 20 else recent_ranges
            avg_recent_range = sum(recent_ranges) / len(recent_ranges) if recent_ranges else 0
            avg_prev_range = sum(prev_ranges) / len(prev_ranges) if prev_ranges else avg_recent_range
            if avg_prev_range > 0 and avg_recent_range / avg_prev_range < 0.7:
                dist_score += 2.5
                dist_desc.append("放量但价格波动收窄，滞涨明显")
            elif avg_prev_range > 0 and avg_recent_range / avg_prev_range < 0.9:
                dist_score += 1
                dist_desc.append("量能放大但价格波动减小")
        
        # 条件4：长上影线
        if avg_upper_shadow > 0.35:
            dist_score += 1.5
            dist_desc.append("频现长上影线，高位抛压重")
        
        # 条件5：顶背驰
        if divergence == "top_divergence":
            dist_score += 2.5
            dist_desc.append("出现顶背驰，上涨动能衰竭")
        
        # 条件6：最近向上笔缩量（上涨无力，仅当有成交量数据时）
        if has_volume and last_stroke["direction"] == "up" and prev_stroke and prev_stroke["direction"] == "down":
            up_vol = stroke_vol_ratio(last_stroke)
            down_vol = stroke_vol_ratio(prev_stroke)
            if down_vol > 0 and up_vol / down_vol < 0.8:
                dist_score += 1.5
                dist_desc.append("最新上涨笔缩量，多头后继乏力")
    
    # 无成交量数据时降低阈值
    dist_threshold = 3 if not has_volume else 4
    if dist_score >= dist_threshold:
        confidence = min(dist_score / 8, 0.95)
        if not has_volume:
            dist_desc.insert(0, "无成交量数据，仅价格形态分析")
        intentions.append({
            "type": "出货",
            "confidence": confidence,
            "desc": "；".join(dist_desc[:3]),
            "color": "#8B0000",  # 暗红
            "icon": "📤"
        })
    
    # ================================================================
    # 6. 拉升检测
    # 特征：连续强势上涨，放量突破中枢，回调极浅，MACD强势
    # ================================================================
    markup_score = 0
    markup_desc = []
    
    if last_stroke and last_pivot:
        zg, zd = last_pivot["zg"], last_pivot["zd"]
        pivot_range = zg - zd
        
        # 条件1：价格在中枢上方
        if current_price > zg and pivot_range > 0:
            dist_above = (current_price - zg) / pivot_range
            if dist_above > 0.5:
                markup_score += 1.5
                markup_desc.append(f"价格突破中枢上沿{zg:.2f}后继续上行")
        
        # 条件2：最近连续上涨笔（至少2笔向上且高点抬升）
        if len(strokes) >= 4:
            recent_up = [s for s in strokes[-6:] if s["direction"] == "up"]
            if len(recent_up) >= 2:
                highs = [s["high"] for s in recent_up]
                if all(highs[i] < highs[i+1] for i in range(len(highs)-1)):
                    markup_score += 2
                    markup_desc.append("上涨笔高点依次抬升，攻势连续")
        
        # 条件3：上涨笔放量（仅当有成交量数据时）
        if has_volume and last_stroke["direction"] == "up":
            up_vol = stroke_vol_ratio(last_stroke)
            if prev_stroke:
                prev_vol = stroke_vol_ratio(prev_stroke)
                if prev_vol > 0 and up_vol / prev_vol > 1.2:
                    markup_score += 2
                    markup_desc.append("上涨笔放量，资金积极做多")
        
        # 条件4：MACD红柱扩张
        if hist_expanding_up:
            markup_score += 2
            markup_desc.append("MACD红柱持续放大，动能强劲")
        elif macd_hist and len(macd_hist) >= 3 and all(macd_hist[-i] > 0 for i in range(1, 4)):
            markup_score += 0.5
            markup_desc.append("MACD维持红柱状态")
        
        # 条件5：最近K线多为阳线
        recent_closes = [k["close"] for k in klines[-8:]]
        recent_opens = [k["open"] for k in klines[-8:]]
        yang_count = sum(1 for i in range(len(recent_closes)) if recent_closes[i] > recent_opens[i])
        if yang_count >= 6:
            markup_score += 1.5
            markup_desc.append(f"近8根K线{yang_count}根阳线，多头占优")
        elif yang_count >= 5:
            markup_score += 0.5
        
        # 条件6：成交量整体放大（仅当有成交量数据时）
        if has_volume and vol_ratio > 1.2:
            markup_score += 1
            markup_desc.append("近期成交量显著放大")
    
    # 无成交量数据时降低阈值
    markup_threshold = 3 if not has_volume else 4
    if markup_score >= markup_threshold:
        confidence = min(markup_score / 8, 0.95)
        if not has_volume:
            markup_desc.insert(0, "无成交量数据，仅价格形态分析")
        intentions.append({
            "type": "拉升",
            "confidence": confidence,
            "desc": "；".join(markup_desc[:3]),
            "color": "#FF4500",  # 橙红
            "icon": "📈"
        })
    
    # ================================================================
    # 7. 打压检测
    # 特征：连续下跌放量，跌破中枢下沿，反弹无力，MACD绿柱扩张
    # ================================================================
    suppress_score = 0
    suppress_desc = []
    
    if last_stroke and last_pivot:
        zg, zd = last_pivot["zg"], last_pivot["zd"]
        pivot_range = zg - zd
        
        # 条件1：价格在中枢下方
        if current_price < zd and pivot_range > 0:
            dist_below = (zd - current_price) / pivot_range
            if dist_below > 0.5:
                suppress_score += 1.5
                suppress_desc.append(f"价格跌破中枢下沿{zd:.2f}后继续下行")
            elif dist_below > 0:
                suppress_score += 0.5
        
        # 条件2：连续下跌笔且低点依次降低
        if len(strokes) >= 4:
            recent_down = [s for s in strokes[-6:] if s["direction"] == "down"]
            if len(recent_down) >= 2:
                lows = [s["low"] for s in recent_down]
                if all(lows[i] > lows[i+1] for i in range(len(lows)-1)):
                    suppress_score += 2
                    suppress_desc.append("下跌笔低点依次降低，空头攻势凌厉")
        
        # 条件3：下跌笔放量（仅当有成交量数据时）
        if has_volume and last_stroke["direction"] == "down":
            down_vol = stroke_vol_ratio(last_stroke)
            if prev_stroke:
                prev_vol = stroke_vol_ratio(prev_stroke)
                if prev_vol > 0 and down_vol / prev_vol > 1.2:
                    suppress_score += 2
                    suppress_desc.append("下跌笔放量，抛压沉重")
        
        # 条件4：MACD绿柱扩张
        if macd_hist and len(macd_hist) >= 3:
            last3 = macd_hist[-3:]
            if all(h < 0 for h in last3) and all(last3[i] <= last3[i-1] for i in range(1, len(last3))):
                suppress_score += 2
                suppress_desc.append("MACD绿柱持续放大，空头动能强劲")
        
        # 条件5：最近K线多为阴线
        recent_closes = [k["close"] for k in klines[-8:]]
        recent_opens = [k["open"] for k in klines[-8:]]
        yin_count = sum(1 for i in range(len(recent_closes)) if recent_closes[i] < recent_opens[i])
        if yin_count >= 6:
            suppress_score += 1.5
            suppress_desc.append(f"近8根K线{yin_count}根阴线，空头占优")
        elif yin_count >= 5:
            suppress_score += 0.5
        
        # 条件6：反弹笔缩量（反弹无力，仅当有成交量数据时）
        if has_volume and last_stroke["direction"] == "up" and prev_stroke and prev_stroke["direction"] == "down":
            up_vol = stroke_vol_ratio(last_stroke)
            down_vol = stroke_vol_ratio(prev_stroke)
            if down_vol > 0 and up_vol / down_vol < 0.7:
                suppress_score += 1.5
                suppress_desc.append("反弹笔明显缩量，多头无力反击")
    
    # 无成交量数据时降低阈值
    suppress_threshold = 3 if not has_volume else 4
    if suppress_score >= suppress_threshold:
        confidence = min(suppress_score / 8, 0.95)
        if not has_volume:
            suppress_desc.insert(0, "无成交量数据，仅价格形态分析")
        intentions.append({
            "type": "打压",
            "confidence": confidence,
            "desc": "；".join(suppress_desc[:3]),
            "color": "#2F4F4F",  # 暗灰绿
            "icon": "📉"
        })
    
    # ================================================================
    # 8. 试盘检测（细化为4种子类型）
    # 参考：主力试盘的四种K线形态
    # - 向上试盘：长上影线，测试抛压
    # - 向下试盘：长下影线，测试支撑
    # - 低开大阳线：测试低位承接
    # - 高开低走：测试高位跟风
    # 特征：时间短、来得猛、去得快，影线长实体小
    # ================================================================
    probe_score = 0
    probe_desc = []
    probe_type = None  # 试盘子类型
    
    if klines and len(klines) >= 5:
        # 分析最近5根K线（试盘动作可能在最近几天内）
        last5_klines = klines[-5:]
        last_k = klines[-1]
        prev_k = klines[-2] if len(klines) >= 2 else None
        
        # 计算最近K线的实体和影线
        def calc_kline_shadow(k):
            """计算K线的实体、上影线、下影线占比"""
            body = abs(k["close"] - k["open"])
            total_range = k["high"] - k["low"]
            if total_range <= 0:
                return {"body_ratio": 1, "upper_ratio": 0, "lower_ratio": 0, "is_yang": k["close"] >= k["open"]}
            upper_shadow = k["high"] - max(k["open"], k["close"])
            lower_shadow = min(k["open"], k["close"]) - k["low"]
            return {
                "body_ratio": body / total_range,
                "upper_ratio": upper_shadow / total_range,
                "lower_ratio": lower_shadow / total_range,
                "is_yang": k["close"] >= k["open"],
                "body": body,
                "upper_shadow": upper_shadow,
                "lower_shadow": lower_shadow,
                "total_range": total_range
            }
        
        last5_info = [calc_kline_shadow(k) for k in last5_klines]
        last_info = last5_info[-1]
        
        # 计算跳空幅度（开盘价相对前收盘价的偏离）
        gap_pct = 0
        if prev_k:
            gap_pct = (last_k["open"] - prev_k["close"]) / prev_k["close"] * 100
        
        # 试盘判定应基于**最后一根K线**（试盘动作来得快去得快）
        # 而不是最近5根中任意一根（那样太宽松）
        
        # ---- 类型1：向上试盘（长上影线试盘）----
        # 特征：最后一根K线出现长上影线冲高回落，测试上方抛压
        # 上影线占比>50%，实体占比<35%，且上影线明显长于下影线
        if last_info["upper_ratio"] > 0.5 and last_info["body_ratio"] < 0.35 and last_info["upper_ratio"] > last_info["lower_ratio"] * 1.5:
            probe_score += 3
            probe_type = "向上试盘"
            probe_desc.append(f"长上影线冲高回落(上影占{last_info['upper_ratio']*100:.0f}%)，试探上方抛压")
            
            # 检查是否在重要压力位附近
            if last_pivot:
                zg = last_pivot["zg"]
                if last_k["high"] >= zg * 0.98:
                    probe_score += 1
                    probe_desc.append(f"触及中枢上沿{zg:.2f}压力区")
        
        # ---- 类型2：向下试盘（长下影线试盘）----
        # 特征：最后一根K线出现长下影线探底回升，测试下方承接
        # 下影线占比>50%，实体占比<35%，且下影线明显长于上影线
        elif last_info["lower_ratio"] > 0.5 and last_info["body_ratio"] < 0.35 and last_info["lower_ratio"] > last_info["upper_ratio"] * 1.5:
            probe_score += 3
            probe_type = "向下试盘"
            probe_desc.append(f"长下影线探底回升(下影占{last_info['lower_ratio']*100:.0f}%)，试探下方支撑")
            
            # 检查是否在重要支撑位附近
            if last_pivot:
                zd = last_pivot["zd"]
                if last_k["low"] <= zd * 1.02:
                    probe_score += 1
                    probe_desc.append(f"触及中枢下沿{zd:.2f}支撑区")
        
        # ---- 类型3：低开大阳线试盘 ----
        # 特征：开盘价大幅低开(>1%)，但收盘大幅拉高形成大阳线
        # 目的：测试低位承接力度，吸引市场注意
        elif gap_pct < -1.0 and last_info["is_yang"] and last_info["body_ratio"] > 0.6:
            # 低开超过1%，且形成大阳线（实体占比>60%）
            body_pct = last_info["body"] / last_k["open"] * 100 if last_k["open"] > 0 else 0
            if body_pct > 1.5:  # 阳线实体幅度>1.5%
                probe_score += 3
                probe_type = "低开大阳试盘"
                probe_desc.append(f"低开{gap_pct:.1f}%后拉出大阳线(实体{body_pct:.1f}%)，测试低位承接")
                
                # 检查成交量是否配合（放量更好）
                if has_volume and volumes[-1] > recent_vol_avg * 1.3:
                    probe_score += 1
                    probe_desc.append("成交量放大，承接盘积极")
        
        # ---- 类型4：高开低走大阴线试盘 ----
        # 特征：开盘价大幅高开(>1%)，但收盘大幅走低形成大阴线
        # 目的：测试高位跟风盘和抛压情况
        elif gap_pct > 1.0 and not last_info["is_yang"] and last_info["body_ratio"] > 0.6:
            body_pct = last_info["body"] / last_k["open"] * 100 if last_k["open"] > 0 else 0
            if body_pct > 1.5:  # 阴线实体幅度>1.5%
                probe_score += 3
                probe_type = "高开低走试盘"
                probe_desc.append(f"高开{gap_pct:+.1f}%后走低收大阴(实体{body_pct:.1f}%)，测试高位抛压")
                
                # 检查是否回落至前收盘价附近
                if abs(last_k["close"] - prev_k["close"]) / prev_k["close"] * 100 < 0.5:
                    probe_score += 1
                    probe_desc.append("收盘回到前收盘价附近，高位压力显现")
        
        # ---- 通用条件 ----
        
        # 条件A：K线实体小影线长（十字星形态，方向试探）
        recent_bodies = [abs(k["close"] - k["open"]) for k in last5_klines]
        recent_ranges = [k["high"] - k["low"] for k in last5_klines]
        if recent_ranges:
            avg_body = sum(recent_bodies) / len(recent_bodies)
            avg_range = sum(recent_ranges) / len(recent_ranges)
            if avg_range > 0 and avg_body / avg_range < 0.25:
                probe_score += 1.5
                if not probe_desc:
                    probe_desc.append("K线实体小影线长，十字星形态，方向试探")
        
        # 条件B：成交量特征（试盘时成交量突然放大后快速萎缩）
        if has_volume and len(volumes) >= 5:
            # 检查是否有"顶天立地"的量柱（突然放量后恢复平静）
            vol_5avg = sum(volumes[-5:]) / 5
            if volumes[-1] > 0 and volumes[-2] > 0:
                max_vol = max(volumes[-5:])
                min_vol = min(volumes[-5:])
                if min_vol > 0 and max_vol / min_vol > 3:
                    probe_score += 1.5
                    probe_desc.append("成交量突增后骤减，试盘特征明显")
        
        # 条件C：价格在中枢附近（试探方向，未明确突破）
        if last_pivot:
            zg, zd = last_pivot["zg"], last_pivot["zd"]
            pivot_center = (zg + zd) / 2
            pivot_range = zg - zd
            if pivot_range > 0 and abs(current_price - pivot_center) / pivot_range < 0.5:
                probe_score += 1.5
                probe_desc.append("价格在中枢附近，方向未明")
        
        # 条件D：MACD接近零轴（多空力量均衡，变盘前兆）
        if macd_hist and len(macd_hist) >= 5:
            recent_hist_abs = [abs(h) for h in macd_hist[-5:]]
            max_hist_abs = max(abs(h) for h in macd_hist[-20:]) if len(macd_hist) >= 20 else max(recent_hist_abs)
            if max_hist_abs > 0 and all(h < max_hist_abs * 0.25 for h in recent_hist_abs):
                probe_score += 1
                probe_desc.append("MACD接近零轴，多空均衡，变盘在即")
        
        # 条件E：试盘时间特征（最近1-3根K线出现，来得快去得快）
        # 检查最近3根K线是否有明显的试盘动作
        if len(last5_info) >= 3:
            last3_max_shadow = max(max(x["upper_ratio"], x["lower_ratio"]) for x in last5_info[-3:])
            if last3_max_shadow > 0.4:
                probe_score += 1
                probe_desc.append("试盘动作出现在近3根K线内，时效性强")
    
    # 试盘是短期行为，周线/月线级别不应轻易触发
    # K线数量少(<=100)说明是周线或月线，提高阈值
    is_long_tf = len(klines) <= 100
    probe_threshold = (5 if is_long_tf else 3) if not has_volume else (5 if is_long_tf else 4)
    if is_long_tf and probe_score >= probe_threshold:
        probe_desc.append("长周期K线，试盘判定更严格")
    if probe_score >= probe_threshold:
        confidence = min(probe_score / 8, 0.95)
        if not has_volume:
            probe_desc.insert(0, "无成交量数据，仅价格形态分析")
        
        # 根据试盘类型设置不同的颜色和图标
        probe_colors = {
            "向上试盘": ("#DAA520", "🔼"),      # 金色，向上箭头
            "向下试盘": ("#DAA520", "🔽"),      # 金色，向下箭头
            "低开大阳试盘": ("#DAA520", "🌅"),  # 金色，日出
            "高开低走试盘": ("#DAA520", "🌇"),  # 金色，日落
        }
        probe_color, probe_icon = probe_colors.get(probe_type, ("#DAA520", "🔍"))
        
        # 构建最终描述
        type_desc = probe_type if probe_type else "试盘"
        final_desc = "；".join(probe_desc[:4])
        
        intentions.append({
            "type": type_desc,
            "confidence": confidence,
            "desc": final_desc,
            "color": probe_color,
            "icon": probe_icon
        })
    
    # ================================================================
    # 9. 护盘检测
    # 特征：价格跌至关键支撑后反弹，下跌缩量，支撑位附近反复震荡
    # ================================================================
    defend_score = 0
    defend_desc = []
    
    if last_pivot and last_stroke:
        zg, zd = last_pivot["zg"], last_pivot["zd"]
        pivot_range = zg - zd
        
        # 条件1：价格接近中枢下沿（在支撑位附近）
        if pivot_range > 0:
            dist_to_zd = (current_price - zd) / pivot_range
            if -0.2 < dist_to_zd < 0.3:  # 在中枢下沿附近
                defend_score += 2
                defend_desc.append(f"价格在中枢下沿{zd:.2f}附近运行")
        
        # 条件2：下跌缩量（仅当有成交量数据时）
        if has_volume and last_stroke["direction"] == "down":
            down_vol = stroke_vol_ratio(last_stroke)
            if prev_stroke:
                prev_vol = stroke_vol_ratio(prev_stroke)
                if prev_vol > 0 and down_vol / prev_vol < 0.7:
                    defend_score += 2
                    defend_desc.append("下跌明显缩量，抛压减弱")
                elif prev_vol > 0 and down_vol / prev_vol < 0.9:
                    defend_score += 1
        
        # 条件3：长下影线（支撑有效）
        if avg_lower_shadow > 0.4:
            defend_score += 2
            defend_desc.append("频现长下影线，下方承接有力")
        
        # 条件4：MACD绿柱缩短（下跌动能减弱）
        if hist_shrinking_down:
            defend_score += 1.5
            defend_desc.append("MACD绿柱缩短，下跌动能减弱")
        
        # 条件5：底背驰
        if divergence == "bottom_divergence":
            defend_score += 2
            defend_desc.append("出现底背驰，下跌动能衰竭")
        
        # 条件6：中枢内低点不创新低（支撑有效）
        if len(strokes) >= 4:
            recent_down = [s for s in strokes[-6:] if s["direction"] == "down"]
            if len(recent_down) >= 2:
                lows = [s["low"] for s in recent_down]
                if lows[-1] >= lows[-2]:
                    defend_score += 1.5
                    defend_desc.append("下跌笔低点未创新低，支撑有效")
    
    # 无成交量数据时降低阈值
    defend_threshold = 3 if not has_volume else 4
    if defend_score >= defend_threshold:
        confidence = min(defend_score / 8, 0.95)
        if not has_volume:
            defend_desc.insert(0, "无成交量数据，仅价格形态分析")
        intentions.append({
            "type": "护盘",
            "confidence": confidence,
            "desc": "；".join(defend_desc[:3]),
            "color": "#4682B4",  # 钢蓝
            "icon": "🛡️"
        })
    
    # ================================================================
    # 10. 放量滞涨检测
    # 特征：成交量明显放大，但价格涨幅很小或下跌，主力可能在出货
    # ================================================================
    if has_volume and len(klines) >= 5:
        last5 = klines[-5:]
        last5_vols = volumes[-5:]
        avg_vol = sum(volumes[-20:-5]) / max(len(volumes[-20:-5]), 1) if len(volumes) > 5 else recent_vol_avg
        
        # 最近5根K线成交量明显放大（>1.5倍）
        recent_vol = sum(last5_vols) / len(last5_vols) if last5_vols else 0
        vol_spike = recent_vol / avg_vol if avg_vol > 0 else 1
        
        # 价格涨幅很小（<0.5%）或下跌
        price_change = (last5[-1]["close"] - last5[0]["open"]) / last5[0]["open"] * 100 if last5[0]["open"] > 0 else 0
        
        if vol_spike > 1.5 and abs(price_change) < 0.5:
            confidence = min(vol_spike * 0.3, 0.85)
            desc_parts = [f"成交量放大{vol_spike:.1f}倍", f"价格仅变动{price_change:+.2f}%"]
            if price_change < 0:
                desc_parts.append("放量下跌，主力出货嫌疑")
            else:
                desc_parts.append("放量滞涨，高位换手迹象")
            if trend == "up":
                desc_parts.append("上涨趋势末端需警惕")
            intentions.append({
                "type": "放量滞涨",
                "confidence": confidence,
                "desc": "；".join(desc_parts[:3]),
                "color": "#FF6347",  # 番茄红
                "icon": "⚠️"
            })
    
    # ================================================================
    # 11. 缩量上涨检测（弱反弹/诱多）
    # 特征：价格上涨但成交量萎缩，上涨动能不足
    # ================================================================
    if has_volume and len(klines) >= 5:
        last5 = klines[-5:]
        last5_vols = volumes[-5:]
        avg_vol = sum(volumes[-20:-5]) / max(len(volumes[-20:-5]), 1) if len(volumes) > 5 else recent_vol_avg
        
        # 价格上涨（>1%）
        price_change = (last5[-1]["close"] - last5[0]["open"]) / last5[0]["open"] * 100 if last5[0]["open"] > 0 else 0
        
        # 成交量萎缩（<0.7倍）
        recent_vol = sum(last5_vols) / len(last5_vols) if last5_vols else 0
        vol_shrink = recent_vol / avg_vol if avg_vol > 0 else 1
        
        if price_change > 1.0 and vol_shrink < 0.7:
            confidence = min((1 - vol_shrink) * 0.8 + price_change * 0.1, 0.8)
            desc_parts = [f"价格上涨{price_change:.1f}%", f"成交量萎缩至{vol_shrink:.0%}"]
            desc_parts.append("缩量上涨，动能不足")
            if trend == "down":
                desc_parts.append("下跌中的反弹，可能是诱多")
            intentions.append({
                "type": "缩量上涨",
                "confidence": confidence,
                "desc": "；".join(desc_parts[:3]),
                "color": "#FFA500",  # 橙色
                "icon": "📉"
            })
    
    # ================================================================
    # 12. 放量下跌检测（恐慌性抛售/主力出货）
    # 特征：成交量放大且价格明显下跌
    # ================================================================
    if has_volume and len(klines) >= 5:
        last5 = klines[-5:]
        last5_vols = volumes[-5:]
        avg_vol = sum(volumes[-20:-5]) / max(len(volumes[-20:-5]), 1) if len(volumes) > 5 else recent_vol_avg
        
        # 价格明显下跌（<-1.5%）
        price_change = (last5[-1]["close"] - last5[0]["open"]) / last5[0]["open"] * 100 if last5[0]["open"] > 0 else 0
        
        # 成交量放大（>1.5倍）
        recent_vol = sum(last5_vols) / len(last5_vols) if last5_vols else 0
        vol_spike = recent_vol / avg_vol if avg_vol > 0 else 1
        
        if price_change < -1.5 and vol_spike > 1.5:
            confidence = min(vol_spike * 0.25 + abs(price_change) * 0.1, 0.85)
            desc_parts = [f"成交量放大{vol_spike:.1f}倍", f"价格下跌{abs(price_change):.1f}%"]
            desc_parts.append("放量下跌，抛压沉重")
            if trend == "up":
                desc_parts.append("上涨趋势可能反转")
            intentions.append({
                "type": "放量下跌",
                "confidence": confidence,
                "desc": "；".join(desc_parts[:3]),
                "color": "#8B0000",  # 深红
                "icon": "🔻"
            })
    
    # ================================================================
    # 13. 利好不涨检测（多头乏力）
    # 特征：出现买入信号或底分型，但价格未能上涨，多头动能不足
    # ================================================================
    if len(klines) >= 5 and divergence == "bottom_divergence":
        # 有底背驰（买入信号）
        last5 = klines[-5:]
        price_change = (last5[-1]["close"] - last5[0]["open"]) / last5[0]["open"] * 100 if last5[0]["open"] > 0 else 0
        
        # 价格涨幅很小或下跌（<0.3%）
        if price_change < 0.3:
            confidence = min(0.5 + abs(price_change) * 0.2, 0.75)
            desc_parts = ["出现底背驰买入信号", f"但价格仅变动{price_change:+.2f}%"]
            desc_parts.append("多头乏力，信号可能失效")
            if has_volume:
                desc_parts.append("需观察成交量配合")
            intentions.append({
                "type": "利好不涨",
                "confidence": confidence,
                "desc": "；".join(desc_parts[:3]),
                "color": "#DAA520",  # 金色
                "icon": "🔕"
            })
    
    # ================================================================
    # 14. 利空不跌检测（空头乏力/主力护盘）
    # 特征：出现卖出信号或顶分型，但价格未能下跌，可能有护盘
    # ================================================================
    if len(klines) >= 5 and divergence == "top_divergence":
        # 有顶背驰（卖出信号）
        last5 = klines[-5:]
        price_change = (last5[-1]["close"] - last5[0]["open"]) / last5[0]["open"] * 100 if last5[0]["open"] > 0 else 0
        
        # 价格跌幅很小或上涨（>-0.3%）
        if price_change > -0.3:
            confidence = min(0.5 + abs(price_change) * 0.2, 0.75)
            desc_parts = ["出现顶背驰卖出信号", f"但价格仅变动{price_change:+.2f}%"]
            desc_parts.append("空头乏力，可能有护盘")
            if has_volume:
                desc_parts.append("关注后续量能变化")
            intentions.append({
                "type": "利空不跌",
                "confidence": confidence,
                "desc": "；".join(desc_parts[:3]),
                "color": "#32CD32",  # 酸橙绿
                "icon": "🔔"
            })
    
    # ================================================================
    # 15. 地量地价检测（变盘前兆）
    # 特征：成交量萎缩至极低水平，价格波动极小，即将变盘
    # ================================================================
    if has_volume and len(klines) >= 10:
        last10_vols = volumes[-10:]
        avg_vol_20 = sum(volumes[-30:-10]) / max(len(volumes[-30:-10]), 1) if len(volumes) > 10 else recent_vol_avg
        
        # 成交量萎缩至极低（<0.4倍）
        recent_vol = sum(last10_vols) / len(last10_vols) if last10_vols else 0
        vol_shrink = recent_vol / avg_vol_20 if avg_vol_20 > 0 else 1
        
        # 价格波动极小（<0.5%）
        last10 = klines[-10:]
        price_range = (max(k["high"] for k in last10) - min(k["low"] for k in last10)) / last10[0]["close"] * 100 if last10[0]["close"] > 0 else 0
        
        if vol_shrink < 0.4 and price_range < 0.5:
            confidence = min((1 - vol_shrink) * 0.6 + (1 - price_range / 0.5) * 0.3, 0.8)
            desc_parts = [f"成交量萎缩至{vol_shrink:.0%}", f"价格波动仅{price_range:.2f}%"]
            desc_parts.append("地量地价，变盘前兆")
            desc_parts.append("密切关注后续突破方向")
            intentions.append({
                "type": "地量地价",
                "confidence": confidence,
                "desc": "；".join(desc_parts[:3]),
                "color": "#808080",  # 灰色
                "icon": "⏳"
            })
    
    # 按置信度排序，最多返回2个意图
    intentions.sort(key=lambda x: -x["confidence"])
    return intentions[:2]


# ============================================================
# 信号生成器
# ============================================================

def derive_trading_points(strokes, segments, pivots, std_pivots, div, div_60m, price):
    """严谨推导缠论三类买卖点（基于线段 / 标准中枢）

    一买/一卖：背驰末端（趋势背驰力度最强，盘整背驰次之）
    二买/二卖：一买/一卖之后回抽不破前低/前高
    三买/三卖：突破中枢后回抽不进中枢（中枢引力边界 ZG/ZD）
    区间套：日线与次级别（如60分钟）同向背驰共振时增强信号。
    返回: {"points": [...], "signal": str, "signal_color": str}
    """
    points = []
    used_pivots = std_pivots if std_pivots else pivots
    last_pivot = used_pivots[-1] if used_pivots else None

    # ---- 一买 / 一卖（背驰末端）----
    if div and div.get("type") != "none":
        t = div["type"]; kind = div.get("kind", "consolidation"); ratio = div.get("ratio", 1.0)
        if t == "bottom":
            strong = (kind == "trend")
            points.append({
                "name": "★一买" if strong else "★一买弱", "type": "buy", "price": price,
                "strength": 1.0 if strong else 0.6,
                "desc": f"底背驰·{kind}，MACD面积比{ratio:.2f}，下跌动能衰竭",
                "color": "#FF4444" if strong else "#FF8888"})
        else:
            strong = (kind == "trend")
            points.append({
                "name": "▲一卖" if strong else "▲一卖弱", "type": "sell", "price": price,
                "strength": 1.0 if strong else 0.6,
                "desc": f"顶背驰·{kind}，MACD面积比{ratio:.2f}，上涨动能衰竭",
                "color": "#4CAF50" if strong else "#88AA88"})

    # ---- 二买 / 二卖（回抽不破前低 / 前高）----
    if len(strokes) >= 4:
        same_dir = [s for s in strokes if s["direction"] == strokes[-1]["direction"]]
        if len(same_dir) >= 2:
            cur = same_dir[-1]
            if cur["direction"] == "up":
                prev_down = next((s for s in reversed(strokes)
                                 if s["direction"] == "down" and s["end"]["index"] < cur["start"]["index"]), None)
                if prev_down and cur["low"] > prev_down["low"]:
                    points.append({"name": "★二买", "type": "buy", "price": price, "strength": 0.8,
                                   "desc": f"回抽低点{cur['low']:.2f}未破前低{prev_down['low']:.2f}",
                                   "color": "#FF6B35"})
            else:
                prev_up = next((s for s in reversed(strokes)
                                if s["direction"] == "up" and s["end"]["index"] < cur["start"]["index"]), None)
                if prev_up and cur["high"] < prev_up["high"]:
                    points.append({"name": "▲二卖", "type": "sell", "price": price, "strength": 0.8,
                                   "desc": f"回抽高点{cur['high']:.2f}未破前高{prev_up['high']:.2f}",
                                   "color": "#2196F3"})

    # ---- 三买 / 三卖（突破中枢后回抽不进中枢）----
    if last_pivot and len(strokes) >= 3:
        zg, zd = last_pivot["zg"], last_pivot["zd"]
        leave_up = next((s for s in strokes if s["high"] > zg), None)
        leave_down = next((s for s in strokes if s["low"] < zd), None)
        if leave_up:
            pb = next((s for s in reversed(strokes)
                       if s["direction"] == "down" and s["end"]["index"] > leave_up["end"]["index"]), None)
            if pb and pb["low"] >= zg:
                points.append({"name": "◆三买", "type": "buy", "price": price, "strength": 0.85,
                               "desc": f"突破中枢上沿{zg:.2f}后回抽{pb['low']:.2f}未进中枢",
                               "color": "#FFA500"})
        if leave_down:
            pb = next((s for s in reversed(strokes)
                       if s["direction"] == "up" and s["end"]["index"] > leave_down["end"]["index"]), None)
            if pb and pb["high"] <= zd:
                points.append({"name": "◆三卖", "type": "sell", "price": price, "strength": 0.85,
                               "desc": f"跌破中枢下沿{zd:.2f}后回抽{pb['high']:.2f}未进中枢",
                               "color": "#9C27B0"})

    # ---- 区间套：日线 + 次级别背驰共振 ----
    if div_60m and div and div.get("type") != "none":
        nested, nlevel = nested_interval_confirm(div, div_60m)
        if nested:
            for p in points:
                if (p["type"] == "buy" and div["type"] == "bottom") or                    (p["type"] == "sell" and div["type"] == "top"):
                    p["strength"] = min(1.0, p["strength"] + 0.1)
                    p["desc"] += f"；{nlevel}"

    buys = [p for p in points if p["type"] == "buy"]
    sells = [p for p in points if p["type"] == "sell"]
    if buys:
        primary = max(buys, key=lambda x: x["strength"])
        return {"points": points, "signal": primary["name"], "signal_color": primary["color"]}
    if sells:
        primary = max(sells, key=lambda x: x["strength"])
        return {"points": points, "signal": primary["name"], "signal_color": primary["color"]}
    return {"points": points, "signal": "●观望", "signal_color": "#888888"}


def analyze_chanlun(klines_daily, klines_weekly=None, klines_60m=None):
    """完整的缠论分析流程，支持多级别联立分析"""
    result = {
        "pivots": [],
        "strokes": [],
        "segments": [],
        "std_pivots": [],
        "signal": "●观望",
        "signal_desc": "数据不足，无法完成缠论分析。\n需要至少15根K线数据。",
        "signal_color": "#888888",
        "divergence": None,
        "divergence_info": {},
        "trend": "unknown",
        "nearest_support": None,
        "nearest_resistance": None,
        "all_pivots_info": [],
        "mm_intention": [],
        "trading_points": [],
        "klines_daily": klines_daily,
    }
    if not klines_daily or len(klines_daily) < 15:
        return result

    # 日K分析（包含处理 → 分型 → 笔 → 线段 → 中枢）
    merged_d = merge_inclusive_klines(klines_daily)
    fractals_d = find_fractals(merged_d)
    strokes_d = build_strokes(fractals_d, merged_d)
    segments_d = build_segments(strokes_d)
    pivots_d = find_pivots(strokes_d)                        # 笔中枢
    std_pivots_d = build_pivots_from_segments(segments_d)     # 线段中枢（标准中枢）
    # MACD 在合并K线上计算，保证与笔索引空间一致（修正背驰区间错位）
    closes_d = [k.get("close", k["high"]) for k in merged_d]
    _, _, hist_d = calc_macd(closes_d)
    divergence_d = check_divergence(strokes_d, hist_d, pivots_d) if strokes_d else \
        {"type": "none", "kind": "none", "ratio": 1.0, "last_area": 0.0, "prev_area": 0.0}

    # 周K分析（如果有）
    pivots_w = []
    std_pivots_w = []
    divergence_w = {"type": "none", "kind": "none", "ratio": 1.0, "last_area": 0.0, "prev_area": 0.0}
    if klines_weekly and len(klines_weekly) >= 15:
        merged_w = merge_inclusive_klines(klines_weekly)
        fractals_w = find_fractals(merged_w)
        strokes_w = build_strokes(fractals_w, merged_w)
        segments_w = build_segments(strokes_w)
        pivots_w = find_pivots(strokes_w)
        std_pivots_w = build_pivots_from_segments(segments_w)
        closes_w = [k.get("close", k["high"]) for k in merged_w]
        _, _, hist_w = calc_macd(closes_w)
        divergence_w = check_divergence(strokes_w, hist_w, pivots_w) if strokes_w else divergence_w

    current_price = klines_daily[-1]["close"]

    # 中枢：周线优先；线段中枢优先于笔中枢
    all_pivots = pivots_w + pivots_d
    used_pivots = (std_pivots_w + std_pivots_d) or all_pivots
    result["pivots"] = all_pivots
    result["strokes"] = strokes_d
    result["segments"] = segments_d
    result["std_pivots"] = std_pivots_d

    # 构建中枢信息列表（区分笔中枢 / 线段中枢）
    for p in (std_pivots_w + std_pivots_d) or all_pivots:
        tag = "线段中枢" if p.get("from_segments") else "笔中枢"
        cnt = p.get("segment_count", p.get("stroke_count"))
        unit = "段" if p.get("from_segments") else "笔"
        result["all_pivots_info"].append(
            f"[{tag}] [{p['zd']:.2f}-{p['zg']:.2f}] "
            f"({p['start_day']}~{p['end_day']}, {cnt}{unit})")

    # 判断趋势（严格定义：至少2个不重叠中枢）
    is_trend_up, pivot_count, trend_strength = is_valid_trend(used_pivots, "up")
    is_trend_down, _, _ = is_valid_trend(used_pivots, "down")

    if is_trend_up:
        result["trend"] = "up"
    elif is_trend_down:
        result["trend"] = "down"
    elif len(used_pivots) >= 2:
        result["trend"] = "consolidation"  # 有中枢但非趋势
    elif len(used_pivots) == 1:
        p = used_pivots[-1]
        if current_price > p["zg"]:
            result["trend"] = "up"
        elif current_price < p["zd"]:
            result["trend"] = "down"
        else:
            result["trend"] = "consolidation"

    # 均线系统辅助分析
    ma_kiss = detect_ma_kiss(closes_d)
    result["ma_kiss"] = ma_kiss

    # 找最近支撑和阻力
    supports = [p["zd"] for p in used_pivots if p["zd"] < current_price]
    resistances = [p["zg"] for p in used_pivots if p["zg"] > current_price]
    # 也加入笔的端点
    for s in strokes_d:
        if s["low"] < current_price:
            supports.append(s["low"])
        if s["high"] > current_price:
            resistances.append(s["high"])
    if supports:
        result["nearest_support"] = max(supports)
    if resistances:
        result["nearest_resistance"] = min(resistances)

    # ---- 严谨买卖点推导（基于线段 / 标准中枢）----
    # 60分钟背驰（用于区间套）
    div_60m = None
    if klines_60m and len(klines_60m) >= 30:
        merged_60m = merge_inclusive_klines(klines_60m)
        strokes_60m = build_strokes(find_fractals(merged_60m), merged_60m)
        pivots_60m = find_pivots(strokes_60m)
        closes_60m = [k.get("close", k["high"]) for k in merged_60m]
        _, _, hist_60m = calc_macd(closes_60m)
        div_60m = check_divergence(strokes_60m, hist_60m, pivots_60m) if strokes_60m else None

    tp = derive_trading_points(strokes_d, segments_d, pivots_d, std_pivots_d,
                               divergence_d, div_60m, current_price)
    points = tp["points"]
    result["trading_points"] = points

    signal = tp["signal"]
    color = tp["signal_color"]
    desc_lines = []
    if points:
        for p in sorted(points, key=lambda x: -x["strength"]):
            desc_lines.append(f"{p['name']}：{p['desc']}（强度 {p['strength']:.0%}）")

    # 无明确买卖点时，按趋势给出偏多/偏空/观望
    if signal == "●观望":
        if result["trend"] == "up":
            signal = "●偏多"; color = "#FF8888"
            desc_lines.append(f"价格 {current_price:.2f} 处于上升趋势，等待回调三买/类二买")
        elif result["trend"] == "down":
            signal = "●偏空"; color = "#88AA88"
            desc_lines.append(f"价格 {current_price:.2f} 处于下降趋势，反弹减仓")
        else:
            desc_lines.append(f"价格 {current_price:.2f} 中枢震荡，观望等待方向选择")

    # 背驰转折分析（一买/一卖成立后）
    if any("一买" in p["name"] for p in points):
        reversal = analyze_divergence_reversal(used_pivots, "bottom_divergence", current_price)
        desc_lines.append(f"背驰转折: {reversal['type']}(概率{reversal['prob']*100:.0f}%) — {reversal['desc']}")
    elif any("一卖" in p["name"] for p in points):
        reversal = analyze_divergence_reversal(used_pivots, "top_divergence", current_price)
        desc_lines.append(f"背驰转折: {reversal['type']}(概率{reversal['prob']*100:.0f}%) — {reversal['desc']}")

    # 均线辅助
    if ma_kiss.get("type") in ("湿吻", "强湿吻"):
        desc_lines.append(f"均线: {ma_kiss['desc']}")

    # 最近中枢区间
    if used_pivots:
        lp = used_pivots[-1]
        desc_lines.append(f"最近中枢 [{lp['zd']:.2f}-{lp['zg']:.2f}]（{lp.get('direction', '')}）")

    # 支撑 / 阻力
    if result["nearest_support"]:
        desc_lines.append(f"支撑 {result['nearest_support']:.2f}")
    if result["nearest_resistance"]:
        desc_lines.append(f"阻力 {result['nearest_resistance']:.2f}")

    # MACD 状态
    if hist_d:
        latest_hist = hist_d[-1]
        prev_hist = hist_d[-2] if len(hist_d) > 1 else 0
        macd_status = "红柱" if latest_hist > 0 else "绿柱"
        strengthening = "放大" if abs(latest_hist) > abs(prev_hist) else "缩短"
        desc_lines.append(f"MACD: {macd_status}{strengthening}")

    # 区间套：日线 + 60分钟背驰共振
    if div_60m and divergence_d and divergence_d.get("type") != "none":
        nested, nlevel = nested_interval_confirm(divergence_d, div_60m)
        if nested:
            desc_lines.append(f"✨ {nlevel}：日线+60分钟背驰共振，信号更可靠")

    result["signal"] = signal
    result["signal_desc"] = "\n".join(desc_lines) if desc_lines else "走势构建中，暂无明确信号"
    result["signal_color"] = color
    result["divergence"] = (divergence_d.get("type") if divergence_d else None)
    result["divergence_info"] = divergence_d or {}
    # 主力意图（兼容旧接口：传字符串）
    div_str = None
    if divergence_d and divergence_d.get("type") == "bottom":
        div_str = "bottom_divergence"
    elif divergence_d and divergence_d.get("type") == "top":
        div_str = "top_divergence"
    result["mm_intention"] = detect_mm_intention(
        klines_daily, strokes_d, all_pivots, hist_d, div_str, result["trend"]
    )
    return result



# ============================================================
# 实时行情获取
# ============================================================

def fetch_realtime(symbols):
    """通过新浪财经API获取实时行情"""
    url = f"https://hq.sinajs.cn/list={','.join(symbols)}"
    req = urllib.request.Request(url, headers={
        "Referer": "https://finance.sina.com.cn",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    })
    try:
        resp = urllib.request.urlopen(req, timeout=8)
        raw = resp.read().decode("gbk")
        results = {}
        for line in raw.strip().split("\n"):
            if not line.strip() or '=""' in line:
                continue
            try:
                # 提取完整标的代码 (如 hf_XAU, sh601899)
                if "hq_str_" in line:
                    code = line.split("hq_str_")[1].split("=")[0]
                else:
                    continue
                vals = line.split('"')[1].split(",")
                if code.startswith("hf"):
                    # 国际行情
                    price = float(vals[0])
                    # 不同品种昨收位置不同:
                    # hf_XAU/XAG: vals[1]=昨收, vals[2]=开盘(=price)
                    # hf_CL/其他: vals[1]=空,   vals[2]=昨收
                    if code in ("hf_XAU", "hf_XAG"):
                        prev = float(vals[1]) if vals[1] else 0
                    else:
                        prev = float(vals[2]) if vals[2] else 0
                    results[code] = {
                        "price": price, "prev_close": prev,
                        "open": float(vals[3]) if vals[3] else 0,
                        "high": float(vals[4]) if vals[4] else 0,
                        "low": float(vals[5]) if vals[5] else 0,
                        "pct": ((price - prev) / prev * 100) if prev > 0 else 0,
                    }
                elif len(vals) >= 32 and vals[3]:
                    # A股
                    price = float(vals[3])
                    prev = float(vals[2])
                    # 如果当前价格为0（未开盘），使用昨收作为显示价格
                    if price == 0 and prev > 0:
                        price = prev
                    results[code] = {
                        "name": vals[0], "price": price, "prev_close": prev,
                        "open": float(vals[1]) if vals[1] else prev,
                        "high": float(vals[4]) if vals[4] else price,
                        "low": float(vals[5]) if vals[5] else price,
                        "pct": ((price - prev) / prev * 100) if prev > 0 else 0,
                    }
            except Exception:
                continue
        return results
    except Exception:
        return None


def fetch_realtime_futures(symbols):
    """通过新浪财经API获取国内期货实时行情
    symbols: ["au0", "ag0", "sc0"]
    """
    # 国内期货在新浪的代码格式: nf_AU0, nf_AG0, nf_SC0
    sina_symbols = [f"nf_{s.upper()}" for s in symbols]
    url = f"https://hq.sinajs.cn/list={','.join(sina_symbols)}"
    req = urllib.request.Request(url, headers={
        "Referer": "https://finance.sina.com.cn",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    })
    try:
        resp = urllib.request.urlopen(req, timeout=8)
        raw = resp.read().decode("gbk")
        results = {}
        for line in raw.strip().split("\n"):
            if not line.strip() or '=""' in line:
                continue
            try:
                # 解析: var hq_str_nf_AU0="名称,开盘,最高,最低,昨收,买价,卖价,最新价,..."
                if "hq_str_" in line:
                    sina_code = line.split("hq_str_")[1].split("=")[0]
                    # nf_AU0 -> au0
                    orig_code = sina_code.replace("nf_", "").lower()
                else:
                    continue
                vals = line.split('"')[1].split(",")
                if len(vals) >= 9:
                    name = vals[0]
                    open_p = float(vals[1]) if vals[1] else 0
                    high = float(vals[2]) if vals[2] else 0
                    low = float(vals[3]) if vals[3] else 0
                    prev = float(vals[4]) if vals[4] else 0
                    price = float(vals[7]) if vals[7] else 0
                    # 如果最新价为0，使用昨收
                    if price == 0 and prev > 0:
                        price = prev
                    if open_p == 0:
                        open_p = price
                    if high == 0:
                        high = price
                    if low == 0:
                        low = price
                    results[orig_code] = {
                        "name": name, "price": price, "prev_close": prev,
                        "open": open_p, "high": high, "low": low,
                        "pct": ((price - prev) / prev * 100) if prev > 0 else 0,
                    }
            except Exception:
                continue
        return results
    except Exception:
        return None


# ============================================================
# GUI 悬浮框
# ============================================================

SIGNAL_COLORS = {
    "★一买": "#FF4444", "★二买": "#FF6B35", "◆三买": "#FFA500",
    "●观望": "#888888", "▲一卖": "#4CAF50", "★★二卖": "#2196F3",
    "★★★三卖": "#9C27B0",
}

class GoldMonitor:
    BG = "#1a1a2e"; BG_ROW = "#16213e"; BG_ROW_ALT = "#1a1a3e"
    FG = "#e0e0e0"; FG_DIM = "#888888"
    UP = "#FF4444"; DOWN = "#4CAF50"; FLAT = "#888888"
    BORDER = "#0f3460"; TITLE_BG = "#0f3460"

    def __init__(self):
        self.root = tk.Tk()
        self.root.title("缠论黄金监控")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.attributes("-alpha", 0.92)
        self.width = 1100
        self.height = 900
        sx = self.root.winfo_screenwidth()
        self.root.geometry(f"{self.width}x{self.height}+{sx - self.width - 20}+30")

        self.rt_data = {}       # 实时行情
        self.analysis = {}      # 缠论分析结果
        self.rows = {}
        self.tooltip_window = None
        self.drag_data = {"x": 0, "y": 0}
        self.running = True
        self.kline_loaded = False
        self._minimized = False
        self._saved_pos = None  # 保存最小化前的位置
        self.positions = load_positions()  # 仓位数据
        self.position_advice = {}  # 仓位建议
        
        # 初始化自动交易（每品种1万元本金）
        auto_data = load_auto_trade()
        if not auto_data:
            auto_data = init_auto_trade()

        self._build_ui()
        # 加载已有交易记录
        self._update_trade_log_display()
        # 保存初始位置
        self.root.update_idletasks()
        self._saved_pos = (self.root.winfo_x(), self.root.winfo_y())
        # 绑定窗口显示/隐藏事件（处理任务栏最小化/恢复）
        self.root.bind("<Map>", self._on_restore)
        self.root.bind("<Unmap>", self._on_minimize_taskbar)
        # 先启动实时行情更新
        self._start_rt_loop()
        # 延迟加载K线并计算缠论
        self.root.after(500, self._load_kline_and_analyze)

    # ---- UI构建 ----

    def _build_ui(self):
        # 如果main_frame已存在，先清空其子控件
        if hasattr(self, 'main_frame') and self.main_frame.winfo_exists():
            for w in self.main_frame.winfo_children():
                w.destroy()
        else:
            # 首次创建main_frame
            self.main_frame = tk.Frame(self.root, bg=self.BG, relief="solid", bd=1,
                                       highlightbackground=self.BORDER, highlightthickness=1)
            self.main_frame.pack(fill="both", expand=True)
        # 标题栏
        title_bar = tk.Frame(self.main_frame, bg=self.TITLE_BG, height=32)
        title_bar.pack(fill="x"); title_bar.pack_propagate(False)
        title_bar.bind("<Button-1>", self._start_drag)
        title_bar.bind("<B1-Motion>", self._do_drag)
        tk.Label(title_bar, text="📊 缠论监控 v2.0", bg=self.TITLE_BG, fg="#FFD700",
                 font=("Microsoft YaHei", 11, "bold")).pack(side="left", padx=8, pady=4)
        self.time_label = tk.Label(title_bar, text="--:--:--", bg=self.TITLE_BG,
                                   fg=self.FG_DIM, font=("Consolas", 9))
        self.time_label.pack(side="left", padx=4)
        bf = tk.Frame(title_bar, bg=self.TITLE_BG); bf.pack(side="right", padx=4)
        tk.Button(bf, text="─", command=self._minimize, bg=self.TITLE_BG, fg=self.FG,
                  font=("Consolas", 9), width=2, bd=0, activebackground="#333").pack(side="left", padx=1)
        tk.Button(bf, text="✕", command=self._close, bg=self.TITLE_BG, fg="#FF6B6B",
                  font=("Consolas", 9), width=2, bd=0, activebackground="#333").pack(side="left", padx=1)
        # 标题栏下方金色装饰线
        tk.Frame(self.main_frame, bg="#FFD700", height=2).pack(fill="x")
        # 表头（字体与数据行逐列一致）
        header = tk.Frame(self.main_frame, bg=self.BG_ROW, height=24)
        header.pack(fill="x"); header.pack_propagate(False)
        header_cols = [
            ("品种",   ("Microsoft YaHei", 9, "bold"), 10, (8,2)),
            ("最新价", ("Consolas", 10, "bold"),          9, 2),
            ("涨跌幅", ("Consolas", 9),                   8, 2),
            ("信号",   ("Microsoft YaHei", 9, "bold"),   7, 2),
            ("意图",   ("Microsoft YaHei", 8, "bold"),   8, 2),
            ("中枢",   ("Microsoft YaHei", 8),            5, 2),
            ("支撑",   ("Consolas", 9),                   7, 2),
            ("阻力",   ("Consolas", 9),                   7, 2),
            ("时预测", ("Consolas", 8),                  16, 1),
            ("日预测", ("Consolas", 8),                  16, 1),
            ("均价",   ("Consolas", 8),                   8, 1),
            ("金额",   ("Consolas", 8),                   8, 1),
            ("操作",   ("Microsoft YaHei", 8, "bold"),   8, 1),
            ("盈利",   ("Consolas", 8, "bold"),           9, 1),
        ]
        for text, ft, w, px in header_cols:
            tk.Label(header, text=text, bg=self.BG_ROW, fg="#FFD700",
                     font=ft, width=w, anchor="w").pack(side="left", padx=px)
        tk.Frame(self.main_frame, bg=self.BORDER, height=1).pack(fill="x")
        # 数据行
        self.data_frame = tk.Frame(self.main_frame, bg=self.BG)
        self.data_frame.pack(fill="both", expand=True)
        for i, code in enumerate(FETCH_ORDER):
            bg = self.BG_ROW if i % 2 == 0 else self.BG_ROW_ALT
            self._create_row(code, SYMBOLS[code]["name"], bg)
        # 状态栏
        tk.Frame(self.main_frame, bg=self.BORDER, height=1).pack(fill="x")
        sb = tk.Frame(self.main_frame, bg=self.TITLE_BG, height=22)
        sb.pack(fill="x"); sb.pack_propagate(False)
        self.status_label = tk.Label(sb, text="正在加载K线数据...", bg=self.TITLE_BG,
                                     fg=self.FG_DIM, font=("Microsoft YaHei", 8), anchor="w")
        self.status_label.pack(side="left", padx=8)
        self.pin_btn = tk.Button(sb, text="📌", command=self._toggle_pin, bg=self.TITLE_BG,
                                 fg="#FFD700", font=("", 9), width=2, bd=0, activebackground="#333")
        self.pin_btn.pack(side="right", padx=4)
        
        # 多品种联立分析区域
        self.group_frame = tk.Frame(self.main_frame, bg="#0f3460", height=60)
        self.group_frame.pack(fill="x", padx=2, pady=2)
        self.group_frame.pack_propagate(False)
        self.group_labels = {}
        self._build_group_display()
        
        # 图例
        lb = tk.Frame(self.main_frame, bg=self.BG, height=20)
        lb.pack(fill="x"); lb.pack_propagate(False)
        for text, color in [("一买","#FF4444"),("二买","#FF6B35"),("三买","#FFA500"),
                            ("偏多","#FF8888"),("观望","#888888"),("偏空","#88AA88"),
                            ("一卖","#4CAF50"),("二卖","#2196F3"),("三卖","#9C27B0")]:
            tk.Label(lb, text=f"●{text}", bg=self.BG, fg=color,
                     font=("Microsoft YaHei", 7)).pack(side="left", padx=3)
        tk.Label(lb, text="｜ 单击行=详情  双击行=缠论K线图", bg=self.BG, fg="#888888",
                 font=("Microsoft YaHei", 7)).pack(side="right", padx=6)
        
        # ---- 自动交易记录面板 ----
        tk.Frame(self.main_frame, bg=self.BORDER, height=1).pack(fill="x")
        trade_header = tk.Frame(self.main_frame, bg=self.TITLE_BG, height=22)
        trade_header.pack(fill="x"); trade_header.pack_propagate(False)
        tk.Label(trade_header, text="📝 交易记录", bg=self.TITLE_BG, fg="#FFD700",
                 font=("Microsoft YaHei", 9, "bold")).pack(side="left", padx=8)
        self.trade_count_label = tk.Label(trade_header, text="", bg=self.TITLE_BG,
                                          fg=self.FG_DIM, font=("Consolas", 8))
        self.trade_count_label.pack(side="right", padx=8)
        
        # 重置按钮
        reset_btn = tk.Label(trade_header, text="↺重置", bg=self.TITLE_BG, fg="#FF8844",
                             font=("Microsoft YaHei", 7, "bold"), cursor="hand2")
        reset_btn.pack(side="right", padx=4)
        reset_btn.bind("<Button-1>", lambda e: self._reset_auto_trade())
        
        # 录入持仓按钮
        add_pos_btn = tk.Label(trade_header, text="+录入持仓", bg=self.TITLE_BG, fg="#4CAF50",
                               font=("Microsoft YaHei", 7, "bold"), cursor="hand2")
        add_pos_btn.pack(side="right", padx=4)
        add_pos_btn.bind("<Button-1>", lambda e: self._add_manual_position())
        
        # ---- 学习引擎状态栏 ----
        self.learn_bar = tk.Frame(self.main_frame, bg="#1a1a2e", height=18)
        self.learn_bar.pack(fill="x"); self.learn_bar.pack_propagate(False)
        self.learn_status_label = tk.Label(self.learn_bar, text="🧠 学习引擎：等待交易数据...",
            bg="#1a1a2e", fg="#888888", font=("Microsoft YaHei", 7))
        self.learn_status_label.pack(side="left", padx=6)
        self.learn_params_label = tk.Label(self.learn_bar, text="",
            bg="#1a1a2e", fg="#AAAAAA", font=("Consolas", 7))
        self.learn_params_label.pack(side="right", padx=6)
        
        # 交易记录列头
        tl_header = tk.Frame(self.main_frame, bg="#1a1a3e", height=20)
        tl_header.pack(fill="x"); tl_header.pack_propagate(False)
        tl_cols = [("时间",12),("品种",8),("操作",6),("价格",9),("数量",6),("金额",8),("盈亏",10),("原因",20)]
        for text, w in tl_cols:
            tk.Label(tl_header, text=text, bg="#1a1a3e", fg="#FFD700",
                     font=("Microsoft YaHei", 7, "bold"), width=w, anchor="w").pack(side="left", padx=2)
        
        # 交易记录滚动区域
        tl_container = tk.Frame(self.main_frame, bg=self.BG, height=80)
        tl_container.pack(fill="x"); tl_container.pack_propagate(False)
        self.trade_log_canvas = tk.Canvas(tl_container, bg=self.BG, highlightthickness=0, height=80)
        tl_scrollbar = tk.Scrollbar(tl_container, orient="vertical", command=self.trade_log_canvas.yview)
        self.trade_log_canvas.config(yscrollcommand=tl_scrollbar.set)
        tl_scrollbar.pack(side="right", fill="y")
        self.trade_log_canvas.pack(side="left", fill="both", expand=True)
        self.trade_log_frame = tk.Frame(self.trade_log_canvas, bg=self.BG)
        self.trade_log_canvas.create_window((0, 0), window=self.trade_log_frame, anchor="nw")
        self.trade_log_frame.bind("<Configure>",
            lambda e: self.trade_log_canvas.configure(scrollregion=self.trade_log_frame.bbox("all")))
        # 鼠标滚轮支持
        self.trade_log_canvas.bind_all("<MouseWheel>",
            lambda e: self.trade_log_canvas.yview_scroll(int(-1*(e.delta/120)), "units"))

    def _build_group_display(self):
        """构建多品种联立分析显示"""
        # 标题行
        title_frame = tk.Frame(self.group_frame, bg="#0f3460")
        title_frame.pack(fill="x", padx=4, pady=2)
        tk.Label(title_frame, text="多品种联立", bg="#0f3460", fg="#FFD700",
                 font=("Microsoft YaHei", 8, "bold")).pack(side="left")
        
        # 品种组显示行
        groups_frame = tk.Frame(self.group_frame, bg="#0f3460")
        groups_frame.pack(fill="x", padx=4, pady=2)
        
        for group_name, group_cfg in COMMODITY_GROUPS.items():
            color = group_cfg["color"]
            # 品种名称
            name_lbl = tk.Label(groups_frame, text=f"{group_name}:", bg="#0f3460", fg=color,
                                font=("Microsoft YaHei", 8, "bold"))
            name_lbl.pack(side="left", padx=(8, 2))
            
            # 综合信号
            signal_lbl = tk.Label(groups_frame, text="--", bg="#0f3460", fg=self.FG,
                                  font=("Microsoft YaHei", 8, "bold"))
            signal_lbl.pack(side="left", padx=2)
            
            # 联动状态
            linkage_lbl = tk.Label(groups_frame, text="", bg="#0f3460", fg=self.FG_DIM,
                                   font=("Microsoft YaHei", 7))
            linkage_lbl.pack(side="left", padx=2)
            
            self.group_labels[group_name] = {
                "signal": signal_lbl,
                "linkage": linkage_lbl
            }

    def _update_group_display(self, group_results):
        """更新多品种联立分析显示"""
        for group_name, result in group_results.items():
            if group_name in self.group_labels:
                labels = self.group_labels[group_name]
                # 更新信号
                signal_text = result["signal"]
                signal_color = result.get("color", self.FG)
                labels["signal"].config(text=signal_text, fg=signal_color)
                # 更新联动状态
                linkage_text = result["linkage"]
                linkage_color = result["linkage_color"]
                labels["linkage"].config(text=linkage_text, fg=linkage_color)

    def _on_analysis_complete(self, count, tf_info, group_info):
        """分析完成后的UI更新（主线程）"""
        self.status_label.config(
            text=f"✅ 缠论分析完成({count}/{len(FETCH_ORDER)}), {tf_info} | 联立: {group_info}")
        # 更新多品种联立显示
        if hasattr(self, 'group_results'):
            self._update_group_display(self.group_results)

    def _show_position_dialog(self, code):
        """仓位设置弹窗"""
        cfg = SYMBOLS.get(code, {})
        name = cfg.get("name", code)
        pos = self.positions.get(code, {})
        
        dialog = tk.Toplevel(self.root)
        dialog.title(f"设置仓位 - {name}")
        dialog.configure(bg=self.BG)
        dialog.attributes("-topmost", True)
        
        # 居中显示
        dialog.geometry("320x240")
        dialog.update_idletasks()
        x = self.root.winfo_x() + (self.width - 320) // 2
        y = self.root.winfo_y() + 100
        dialog.geometry(f"320x240+{x}+{y}")
        
        # 标题
        tk.Label(dialog, text=f"{name} 仓位设置", bg=self.BG, fg="#FFD700",
                 font=("Microsoft YaHei", 11, "bold")).pack(pady=10)
        
        # 持仓成本
        f1 = tk.Frame(dialog, bg=self.BG)
        f1.pack(fill="x", padx=20, pady=5)
        tk.Label(f1, text="持仓成本:", bg=self.BG, fg=self.FG,
                 font=("Microsoft YaHei", 9)).pack(side="left")
        cost_var = tk.StringVar(value=str(pos.get("cost_price", "")))
        cost_entry = tk.Entry(f1, textvariable=cost_var, bg="#16213e", fg=self.FG,
                              font=("Consolas", 10), width=12, insertbackground=self.FG,
                              relief="flat", bd=2)
        cost_entry.pack(side="right")
        
        # 持仓金额
        f2 = tk.Frame(dialog, bg=self.BG)
        f2.pack(fill="x", padx=20, pady=5)
        tk.Label(f2, text="持仓金额(元):", bg=self.BG, fg=self.FG,
                 font=("Microsoft YaHei", 9)).pack(side="left")
        value_var = tk.StringVar(value=str(pos.get("position_value", "")))
        value_entry = tk.Entry(f2, textvariable=value_var, bg="#16213e", fg=self.FG,
                               font=("Consolas", 10), width=12, insertbackground=self.FG,
                               relief="flat", bd=2)
        value_entry.pack(side="right")
        
        # 当前价格显示
        current_price = self.rt_data.get(code, {}).get("price", 0)
        tk.Label(dialog, text=f"当前价格: {current_price:.2f}", bg=self.BG, fg=self.FG_DIM,
                 font=("Microsoft YaHei", 8)).pack(pady=5)
        
        # 按钮
        def on_save():
            try:
                cost = float(cost_var.get()) if cost_var.get() else 0
                value = float(value_var.get()) if value_var.get() else 0
                self.positions[code] = {
                    "cost_price": cost,
                    "position_value": value
                }
                save_positions(self.positions)
                # 立即更新建议
                self._update_single_position_advice(code)
                dialog.destroy()
            except ValueError:
                pass
        
        def on_clear():
            if code in self.positions:
                del self.positions[code]
                save_positions(self.positions)
            if code in self.rows:
                self.rows[code]["pos_cost"].config(text="--", fg=self.FG_DIM)
                self.rows[code]["pos_value"].config(text="--", fg=self.FG_DIM)
                self.rows[code]["pos_action"].config(text="--", fg=self.FG_DIM)
            dialog.destroy()
        
        bf = tk.Frame(dialog, bg=self.BG)
        bf.pack(pady=10)
        tk.Button(bf, text="保存", command=on_save, bg="#0f3460", fg="#FFD700",
                  font=("Microsoft YaHei", 9, "bold"), width=8, relief="flat",
                  activebackground="#1a4080").pack(side="left", padx=5)
        tk.Button(bf, text="清除", command=on_clear, bg="#333", fg=self.FG_DIM,
                  font=("Microsoft YaHei", 9), width=8, relief="flat",
                  activebackground="#555").pack(side="left", padx=5)
        tk.Button(bf, text="取消", command=dialog.destroy, bg="#333", fg=self.FG_DIM,
                  font=("Microsoft YaHei", 9), width=8, relief="flat",
                  activebackground="#555").pack(side="left", padx=5)
        
        cost_entry.focus_set()

    def _update_trade_log_display(self):
        """更新交易记录显示"""
        if not hasattr(self, 'trade_log_frame'):
            return
        # 清空现有记录
        for w in self.trade_log_frame.winfo_children():
            w.destroy()
        
        log_list = load_trade_log()
        if not log_list:
            tk.Label(self.trade_log_frame, text="暂无交易记录", bg=self.BG, fg=self.FG_DIM,
                     font=("Microsoft YaHei", 8)).pack(pady=4)
            self.trade_count_label.config(text="")
            return
        
        # 更新交易计数
        self.trade_count_label.config(text=f"共{len(log_list)}笔")
        
        # 倒序显示（最新在前）
        for i, record in enumerate(reversed(log_list)):
            bg = self.BG_ROW if i % 2 == 0 else self.BG_ROW_ALT
            row = tk.Frame(self.trade_log_frame, bg=bg)
            row.pack(fill="x", padx=1, pady=0)
            
            # 操作颜色
            action = record.get("action", "")
            if "建" in action or "加" in action or "试" in action:
                action_color = "#FF4444"
            elif "减" in action or "止" in action or "损" in action or "出" in action:
                action_color = "#4CAF50"
            else:
                action_color = self.FG
            
            # 盈亏颜色
            total_pnl = record.get("total_pnl", 0)
            total_pnl_pct = record.get("total_pnl_pct", 0)
            pnl_color = self.UP if total_pnl >= 0 else self.DOWN
            pnl_sign = "+" if total_pnl >= 0 else ""
            
            cols = [
                (record.get("time", ""), self.FG_DIM, 12),
                (record.get("name", ""), self.FG, 8),
                (action, action_color, 6),
                (fmt_price(record.get('price', 0)), self.FG, 9),
                (str(record.get("shares", 0)), self.FG, 6),
                (f"{record.get('amount', 0):.0f}", self.FG, 8),
                (f"{pnl_sign}{total_pnl:.0f}({pnl_sign}{total_pnl_pct:.1f}%)", pnl_color, 10),
                (record.get("reason", ""), self.FG_DIM, 20),
            ]
            for text, color, w in cols:
                tk.Label(row, text=text, bg=bg, fg=color,
                         font=("Consolas", 7), width=w, anchor="w").pack(side="left", padx=2)
        
        # 同时更新学习引擎状态
        self._update_learn_display()
    
    def _update_learn_display(self):
        """更新学习引擎状态栏显示"""
        if not hasattr(self, 'learn_status_label'):
            return
        try:
            params = load_learn_params()
            history = load_trade_history()
            total_analyzed = params.get("total_trades_analyzed", 0)
            win_rate = params.get("recent_win_rate", 0)
            avg_pnl = params.get("recent_avg_pnl", 0)
            last_update = params.get("last_update", "")
            
            if total_analyzed == 0:
                # 还未学习过
                remaining = LEARN_INTERVAL - len(history) % LEARN_INTERVAL
                if remaining == LEARN_INTERVAL:
                    remaining = 0
                self.learn_status_label.config(
                    text=f"🧠 学习引擎：已记录{len(history)}笔，还需{remaining}笔触发学习",
                    fg="#888888")
                self.learn_params_label.config(text="")
            else:
                # 已学习过
                wr_color = "#4CAF50" if win_rate >= 0.5 else "#FF4444"
                wr_text = f"胜率{win_rate*100:.0f}%"
                pnl_sign = "+" if avg_pnl >= 0 else ""
                pnl_text = f"均盈{pnl_sign}{avg_pnl:.1f}%"
                
                self.learn_status_label.config(
                    text=f"🧠 已学习{total_analyzed}笔 | {wr_text} | {pnl_text} | 更新:{last_update[:10]}",
                    fg="#4CAF50" if win_rate >= 0.5 else "#FF8844")
                
                # 显示关键参数调整
                parts = []
                sl = params.get("stop_loss_pct", -10)
                if sl != -10:
                    parts.append(f"止损{sl:.0f}%")
                mult = params.get("buy_ratio_multiplier", 1.0)
                if abs(mult - 1.0) > 0.01:
                    parts.append(f"仓位x{mult:.2f}")
                sig_adjs = params.get("signal_adjustments", {})
                if sig_adjs:
                    top_sig = max(sig_adjs.items(), key=lambda x: abs(x[1]))
                    parts.append(f"{top_sig[0]}{'+'if top_sig[1]>0 else ''}{top_sig[1]:.1f}")
                self.learn_params_label.config(text=" | ".join(parts) if parts else "参数未调整")
        except Exception:
            self.learn_status_label.config(text="🧠 学习引擎：初始化中...", fg="#888888")

    def _reset_auto_trade(self):
        """重置自动交易持仓记录（带确认对话框）"""
        from tkinter import messagebox
        result = messagebox.askyesno(
            "确认重置",
            "确定要重置所有自动交易记录吗？\n\n"
            "将清除：\n"
            "• 所有品种的持仓和现金\n"
            "• 交易记录日志\n"
            "• 交易历史（学习引擎用）\n"
            "• 学习参数（恢复默认）\n\n"
            "每个品种将重新分配10,000元本金。",
            icon=messagebox.WARNING
        )
        if not result:
            return
        
        # 删除所有交易相关文件
        files_to_clear = [
            AUTO_TRADE_FILE,
            TRADE_LOG_FILE,
            TRADE_HISTORY_FILE,
            LEARN_PARAMS_FILE,
        ]
        for fp in files_to_clear:
            if os.path.exists(fp):
                try:
                    os.remove(fp)
                except Exception:
                    pass
        
        # 重新初始化交易数据
        init_auto_trade()
        
        # 刷新UI显示
        self._update_trade_log_display()
        self._update_learn_display()
        
        # 更新所有品种的持仓显示
        for code in SYMBOLS:
            if code in self.rows:
                self.rows[code]["pos_cost"].config(text="空仓", fg=self.FG_DIM)
                self.rows[code]["pos_value"].config(text=f"现金{INITIAL_CAPITAL}元", fg=self.FG_DIM)
                self.rows[code]["pos_action"].config(text="", fg=self.FG_DIM)
                self.rows[code]["pnl_label"].config(text="+0元(+0.0%)", fg=self.FG_DIM)
        
        messagebox.showinfo("重置完成", "所有交易记录已重置！\n每个品种重新分配10,000元本金。")

    def _add_manual_position(self):
        """手动录入持仓，纳入自动交易管理"""
        from tkinter import messagebox, ttk
        
        dialog = tk.Toplevel(self.root)
        dialog.title("录入持仓")
        dialog.geometry("360x320")
        dialog.configure(bg=self.BG)
        dialog.resizable(False, False)
        dialog.transient(self.root)
        dialog.grab_set()
        
        # 标题
        tk.Label(dialog, text="录入已有持仓", bg=self.BG, fg="#FFD700",
                 font=("Microsoft YaHei", 12, "bold")).pack(pady=(15, 5))
        tk.Label(dialog, text="录入后自动纳入交易系统管理（可自动卖出/买入）",
                 bg=self.BG, fg=self.FG_DIM, font=("Microsoft YaHei", 8)).pack(pady=(0, 10))
        
        # 品种选择
        frame1 = tk.Frame(dialog, bg=self.BG)
        frame1.pack(fill="x", padx=20, pady=5)
        tk.Label(frame1, text="品种:", bg=self.BG, fg=self.FG,
                 font=("Microsoft YaHei", 9), width=6, anchor="w").pack(side="left")
        
        code_list = [f"{code} - {SYMBOLS[code]['name']}" for code in FETCH_ORDER]
        code_var = tk.StringVar(value=code_list[0])
        code_combo = ttk.Combobox(frame1, textvariable=code_var, values=code_list,
                                   state="readonly", width=22, font=("Microsoft YaHei", 9))
        code_combo.pack(side="left", padx=5)
        
        # 持仓数量
        frame2 = tk.Frame(dialog, bg=self.BG)
        frame2.pack(fill="x", padx=20, pady=5)
        tk.Label(frame2, text="数量:", bg=self.BG, fg=self.FG,
                 font=("Microsoft YaHei", 9), width=6, anchor="w").pack(side="left")
        shares_entry = tk.Entry(frame2, font=("Consolas", 11), width=14, bg="#1a1a3e",
                                fg="#FFFFFF", insertbackground="#FFFFFF")
        shares_entry.pack(side="left", padx=5)
        tk.Label(frame2, text="股/单位", bg=self.BG, fg=self.FG_DIM,
                 font=("Microsoft YaHei", 8)).pack(side="left")
        
        # 持仓均价
        frame3 = tk.Frame(dialog, bg=self.BG)
        frame3.pack(fill="x", padx=20, pady=5)
        tk.Label(frame3, text="均价:", bg=self.BG, fg=self.FG,
                 font=("Microsoft YaHei", 9), width=6, anchor="w").pack(side="left")
        price_entry = tk.Entry(frame3, font=("Consolas", 11), width=14, bg="#1a1a3e",
                               fg="#FFFFFF", insertbackground="#FFFFFF")
        price_entry.pack(side="left", padx=5)
        tk.Label(frame3, text="元", bg=self.BG, fg=self.FG_DIM,
                 font=("Microsoft YaHei", 8)).pack(side="left")
        
        # 买入日期（T+1用）
        frame4 = tk.Frame(dialog, bg=self.BG)
        frame4.pack(fill="x", padx=20, pady=5)
        tk.Label(frame4, text="买入日:", bg=self.BG, fg=self.FG,
                 font=("Microsoft YaHei", 9), width=6, anchor="w").pack(side="left")
        date_entry = tk.Entry(frame4, font=("Consolas", 11), width=14, bg="#1a1a3e",
                              fg="#FFFFFF", insertbackground="#FFFFFF")
        date_entry.insert(0, time.strftime("%Y-%m-%d"))
        date_entry.pack(side="left", padx=5)
        tk.Label(frame4, text="格式:YYYY-MM-DD", bg=self.BG, fg=self.FG_DIM,
                 font=("Microsoft YaHei", 7)).pack(side="left")
        
        # 按钮区
        btn_frame = tk.Frame(dialog, bg=self.BG)
        btn_frame.pack(fill="x", padx=20, pady=(15, 10))
        
        def do_save():
            selected = code_var.get()
            code = selected.split(" - ")[0].strip()
            code_name = SYMBOLS.get(code, {}).get("name", code)
            
            try:
                shares = int(shares_entry.get().strip())
                cost_price = float(price_entry.get().strip())
                buy_date = date_entry.get().strip()
            except ValueError:
                messagebox.showerror("输入错误", "请输入有效的数字！", parent=dialog)
                return
            
            if shares <= 0 or cost_price <= 0:
                messagebox.showerror("输入错误", "数量和均价必须大于0！", parent=dialog)
                return
            
            # 更新交易数据
            trade_data = load_auto_trade()
            if code not in trade_data:
                trade_data[code] = {
                    "cost_price": 0, "shares": 0, "cash": INITIAL_CAPITAL,
                    "initial_capital": INITIAL_CAPITAL, "realized_pnl": 0,
                    "max_price": 0, "trade_count": 0, "last_action": "",
                    "buy_date": "", "entry_info": {}
                }
            
            pos = trade_data[code]
            pos["shares"] = shares
            pos["cost_price"] = cost_price
            pos["max_price"] = cost_price  # 初始化最高价为均价
            pos["buy_date"] = buy_date
            pos["last_action"] = "手动录入"
            # 调整现金：扣除持仓市值
            position_value = shares * cost_price
            pos["cash"] = max(pos["initial_capital"] - position_value, 0)
            # 清空入场信息（手动录入的不记录入场条件）
            pos["entry_info"] = {}
            
            save_auto_trade(trade_data)
            
            # 记录交易日志
            append_trade_log(code, code_name, "手动录入", cost_price, shares,
                             position_value, f"手动录入持仓{shares}单位@{fmt_price(cost_price)}",
                             pos["cash"], pos["realized_pnl"],
                             (pos["cash"] + position_value - pos["initial_capital"]) / pos["initial_capital"] * 100)
            
            # 刷新UI
            self._update_trade_log_display()
            self._update_learn_display()
            
            # 更新该品种的持仓显示
            if code in self.rows:
                self.rows[code]["pos_cost"].config(text=fmt_price(cost_price), fg=self.FG)
                pnl_pct_now = 0
                val_text = f"{position_value:.0f}元 {pnl_pct_now:+.1f}%"
                self.rows[code]["pos_value"].config(text=val_text, fg=self.FG)
                self.rows[code]["pos_action"].config(text="手动录入", fg="#4CAF50")
            
            messagebox.showinfo("录入成功",
                                f"{code_name} 持仓已录入！\n"
                                f"数量: {shares}  |  均价: {fmt_price(cost_price)}\n"
                                f"系统将根据实时行情自动管理该持仓。",
                                parent=dialog)
            dialog.destroy()
        
        tk.Button(btn_frame, text="确认录入", bg="#4CAF50", fg="white",
                  font=("Microsoft YaHei", 9, "bold"), width=12,
                  relief="flat", cursor="hand2", command=do_save).pack(side="left", padx=10)
        tk.Button(btn_frame, text="取消", bg="#555555", fg="white",
                  font=("Microsoft YaHei", 9), width=8,
                  relief="flat", cursor="hand2", command=dialog.destroy).pack(side="left", padx=10)

    def _notify_trade(self, code, action, trade_result):
        """自动交易弹框提示（避免重复弹窗）"""
        if not hasattr(self, '_last_trade_notify'):
            self._last_trade_notify = set()
        
        # 生成唯一标识，避免同一笔交易重复提示
        notify_key = f"{code}_{action}_{trade_result.get('price', 0)}"
        if notify_key in self._last_trade_notify:
            return
        self._last_trade_notify.add(notify_key)
        # 只保留最近20条记录，防止内存无限增长
        if len(self._last_trade_notify) > 20:
            self._last_trade_notify = set(list(self._last_trade_notify)[-20:])
        
        code_name = SYMBOLS.get(code, {}).get("name", code)
        price = trade_result.get("price", 0)
        shares = trade_result.get("shares", 0)
        reason = trade_result.get("reason", "")
        total_pnl = trade_result.get("total_pnl", 0)
        total_pnl_pct = trade_result.get("total_pnl_pct", 0)
        
        # 根据操作类型设置图标和颜色
        if "建" in action or "加" in action or "试" in action:
            icon = "📈"
            color_tag = "买入"
        elif "止" in action or "损" in action or "出" in action:
            icon = "📉"
            color_tag = "卖出"
        elif "减" in action:
            icon = "⬇️"
            color_tag = "减仓"
        else:
            icon = "🔔"
            color_tag = "操作"
        
        pnl_sign = "+" if total_pnl >= 0 else ""
        
        msg = (
            f"{icon} {code_name} {color_tag}\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"操作: {action}\n"
            f"价格: {fmt_price(price)}\n"
            f"数量: {shares}\n"
            f"原因: {reason}\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"总资产盈亏: {pnl_sign}{total_pnl:.0f}元 ({pnl_sign}{total_pnl_pct:.1f}%)"
        )
        
        # 延迟弹出，避免阻塞当前刷新周期
        self.root.after(100, lambda: messagebox.showinfo(f"自动交易 - {code_name}", msg))

    def _update_single_position_advice(self, code):
        """更新单个品种的仓位建议（自动交易模式）"""
        if code not in self.rows:
            return
        data = self.rt_data.get(code, {})
        ana = self.analysis.get(code, {})
        current_price = data.get("price", 0)
        signal = ana.get("signal", "●观望")
        
        if current_price <= 0:
            return
        
        # 执行自动交易
        trade_result = auto_trade_execute(code, current_price, signal, ana)
        if not trade_result:
            return
        
        cost_price = trade_result["cost_price"]
        position_value = trade_result["position_value"]
        total_pnl = trade_result["total_pnl"]
        total_pnl_pct = trade_result["total_pnl_pct"]
        cash = trade_result["cash"]
        total_assets = trade_result["total_assets"]
        action = trade_result["action"]
        
        # 更新均价显示
        if cost_price > 0:
            self.rows[code]["pos_cost"].config(text=fmt_price(cost_price), fg=self.FG)
        else:
            self.rows[code]["pos_cost"].config(text="空仓", fg=self.FG_DIM)
        
        # 更新金额显示（持仓市值 + 浮动盈亏）
        if position_value > 0:
            pnl_pct = (current_price - cost_price) / cost_price * 100 if cost_price > 0 else 0
            pnl_sign = "+" if pnl_pct >= 0 else ""
            val_text = f"{position_value:.0f}元 {pnl_sign}{pnl_pct:.1f}%"
            pnl_color = self.UP if pnl_pct >= 0 else self.DOWN
            self.rows[code]["pos_value"].config(text=val_text, fg=pnl_color)
        else:
            val_text = f"现金{cash:.0f}元"
            self.rows[code]["pos_value"].config(text=val_text, fg=self.FG_DIM)
        
        # 更新操作建议显示
        action_color = "#FF4444" if "加" in action or "建" in action or "试" in action else \
                       "#4CAF50" if "减" in action or "止" in action else "#888888"
        action_text = action if action != "持有" else "持有"
        self.rows[code]["pos_action"].config(text=action_text, fg=action_color)
        
        # 更新盈利显示
        pnl_sign = "+" if total_pnl >= 0 else ""
        pnl_text = f"{pnl_sign}{total_pnl:.0f}元({pnl_sign}{total_pnl_pct:.1f}%)"
        if total_pnl >= 0:
            pnl_color = self.UP
            if total_pnl_pct >= 50:
                pnl_color = "#FFD700"  # 金色：达成50%目标
        else:
            pnl_color = self.DOWN
        self.rows[code]["pnl_label"].config(text=pnl_text, fg=pnl_color)
        
        # 有新交易时更新交易记录显示
        if action not in ["持有", "--", ""]:
            self._update_trade_log_display()
            # 弹框提示自动交易
            self._notify_trade(code, action, trade_result)

    @staticmethod
    def _contrast(hex_color):
        """根据背景色亮度返回黑/白文字色，保证徽章可读性"""
        h = hex_color.lstrip("#")
        if len(h) != 6:
            return "#ffffff"
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        # 相对亮度（感知加权）
        lum = (0.299 * r + 0.587 * g + 0.114 * b) / 255.0
        return "#101010" if lum > 0.6 else "#ffffff"

    def _make_pill(self, parent, text, color, bg):
        """生成一个彩色药丸(Pill)徽章：外层Frame贴合行背景，内层Label为彩色徽章"""
        f = tk.Frame(parent, bg=bg)
        lbl = tk.Label(f, text=text, bg=color, fg=self._contrast(color),
                       font=("Microsoft YaHei", 8, "bold"), padx=6, pady=2)
        lbl.pack()
        return f, lbl

    def _create_row(self, code, name, bg):
        row = tk.Frame(self.data_frame, bg=bg, cursor="hand2")
        row.pack(fill="x", pady=0)
        row.bind("<Button-1>", lambda e, c=code: self._show_tooltip(c))
        row.bind("<Double-Button-1>", lambda e, c=code: self._show_chart(c))
        nl = tk.Label(row, text=name, bg=bg, fg=self.FG, font=("Microsoft YaHei", 9, "bold"),
                      anchor="w", width=10); nl.pack(side="left", padx=(8,2), pady=6)
        pl = tk.Label(row, text="--", bg=bg, fg=self.FG, font=("Consolas", 10, "bold"),
                      anchor="w", width=9); pl.pack(side="left", padx=2, pady=6)
        pcl = tk.Label(row, text="--", bg=bg, fg=self.FG, font=("Consolas", 9),
                       anchor="w", width=8); pcl.pack(side="left", padx=2, pady=6)
        sl_frame, sl = self._make_pill(row, "--", self.FG_DIM, bg)
        sl_frame.pack(side="left", padx=2, pady=6)
        # 主力意图
        mm_lbl = tk.Label(row, text="--", bg=bg, fg=self.FG_DIM, font=("Microsoft YaHei", 8),
                          anchor="w", width=8); mm_lbl.pack(side="left", padx=2, pady=6)
        # 中枢数量
        pvl = tk.Label(row, text="--", bg=bg, fg="#87CEEB", font=("Microsoft YaHei", 8),
                       anchor="w", width=5); pvl.pack(side="left", padx=2, pady=6)
        # 支撑位
        spl = tk.Label(row, text="--", bg=bg, fg=self.DOWN, font=("Consolas", 9),
                       anchor="w", width=7); spl.pack(side="left", padx=2, pady=6)
        # 阻力位
        rsl = tk.Label(row, text="--", bg=bg, fg=self.UP, font=("Consolas", 9),
                       anchor="w", width=7); rsl.pack(side="left", padx=2, pady=6)
        # 预测-小时级别
        prl_h = tk.Label(row, text="--", bg=bg, fg=self.FG_DIM, font=("Consolas", 8),
                         anchor="w", width=16); prl_h.pack(side="left", padx=1, pady=6)
        # 预测-日线级别
        prl_d = tk.Label(row, text="--", bg=bg, fg=self.FG_DIM, font=("Consolas", 8),
                         anchor="w", width=16); prl_d.pack(side="left", padx=1, pady=6)
        # 仓位-均价（左键打开设置弹窗）
        pos_cost = tk.Label(row, text="--", bg=bg, fg=self.FG_DIM, font=("Consolas", 8),
                            anchor="w", width=8, cursor="hand2")
        pos_cost.pack(side="left", padx=1, pady=6)
        pos_cost.bind("<Button-1>", lambda e, c=code: self._show_position_dialog(c))
        # 仓位-金额
        pos_value = tk.Label(row, text="--", bg=bg, fg=self.FG_DIM, font=("Consolas", 8),
                             anchor="w", width=14, cursor="hand2")
        pos_value.pack(side="left", padx=1, pady=6)
        pos_value.bind("<Button-1>", lambda e, c=code: self._show_position_dialog(c))
        # 仓位-操作建议
        pos_action = tk.Label(row, text="--", bg=bg, fg=self.FG_DIM, font=("Microsoft YaHei", 8, "bold"),
                              anchor="w", width=8, cursor="hand2")
        pos_action.pack(side="left", padx=1, pady=6)
        pos_action.bind("<Button-1>", lambda e, c=code: self._show_position_dialog(c))
        # 盈利情况
        pnl_label = tk.Label(row, text="--", bg=bg, fg=self.FG_DIM, font=("Consolas", 8, "bold"),
                             anchor="w", width=9)
        pnl_label.pack(side="left", padx=1, pady=6)
        for w in [nl, pl, pcl, sl, mm_lbl, pvl, spl, rsl, prl_h, prl_d]:
            w.bind("<Button-1>", lambda e, c=code: self._show_tooltip(c))
            w.bind("<Double-Button-1>", lambda e, c=code: self._show_chart(c))
        self.rows[code] = {"row": row, "name": nl, "price": pl, "pct": pcl,
                           "signal": sl, "mm_intention": mm_lbl, "pivot": pvl, "support": spl, "resistance": rsl,
                           "pred_h": prl_h, "pred_d": prl_d,
                           "pos_cost": pos_cost, "pos_value": pos_value, "pos_action": pos_action,
                           "pnl_label": pnl_label, "bg": bg}

    # ---- K线图窗口（缠论结构可视化）----
    def _show_chart(self, code):
        """双击行打开：绘制日线K线 + 笔 + 线段 + 中枢 + 买卖点 + MACD"""
        try:
            import matplotlib
            matplotlib.use("TkAgg")
            import matplotlib.font_manager as fm
            # 优先使用系统中文字体，避免图表中文乱码/缺字
            for cjk in ("Noto Sans CJK SC", "WenQuanYi Zen Hei", "Microsoft YaHei",
                        "SimHei", "PingFang SC", "Source Han Sans SC"):
                if any(cjk in f.name for f in fm.fontManager.ttflist):
                    matplotlib.rcParams["font.sans-serif"] = \
                        [cjk] + matplotlib.rcParams.get("font.sans-serif", [])
                    matplotlib.rcParams["axes.unicode_minus"] = False
                    break
            import matplotlib.pyplot as plt
            from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
        except Exception:
            return
        ana = self.analysis.get(code)
        cfg = SYMBOLS.get(code)
        if not ana or not ana.get("klines_daily"):
            return
        klines = ana["klines_daily"]
        daily = ana.get("timeframes", {}).get("daily", ana)
        merged = merge_inclusive_klines(klines)
        if len(merged) < 5:
            return
        strokes = daily.get("strokes", [])
        segments = daily.get("segments", [])
        used_pivots = (daily.get("std_pivots") or daily.get("pivots") or [])
        tps = daily.get("trading_points", [])
        closes_m = [k.get("close", k["high"]) for k in merged]
        N = len(merged)

        win = tk.Toplevel(self.root)
        win.title(f"{cfg['name'] if cfg else code} · 缠论K线图")
        win.attributes("-topmost", True)
        win.configure(bg="#0d1117")
        fig = plt.Figure(figsize=(12, 7), dpi=100)
        fig.patch.set_facecolor("#0d1117")

        # 价格子图
        ax = fig.add_subplot(2, 1, 1)
        ax.set_facecolor("#0d1117")
        opens = [k.get("open", k["close"]) for k in merged]
        for i, k in enumerate(merged):
            o, c, h, l = opens[i], closes_m[i], k["high"], k["low"]
            col = "#FF4D4D" if c >= o else "#2ECC71"
            ax.plot([i, i], [l, h], color=col, linewidth=0.7, alpha=0.85)
            ax.plot([i - 0.32, i + 0.32], [o, o], color=col, linewidth=1.4)
            ax.plot([i - 0.32, i + 0.32], [c, c], color=col, linewidth=1.4)

        # 笔（金色实线）
        for s in strokes:
            si, ei = s["start"]["index"], s["end"]["index"]
            ax.plot([si, ei], [s["start"]["value"], s["end"]["value"]],
                    color="#FFD700", linewidth=1.5, alpha=0.95, zorder=5)
        # 线段（蓝色虚线，更粗）
        for seg in segments:
            si, ei = seg["start_idx"], seg["end_idx"]
            if ei > si:
                ax.plot([si, ei], [seg["high"], seg["low"]], color="#4FC3F7",
                        linewidth=2.2, linestyle="--", alpha=0.85, zorder=4)
        # 中枢（紫色半透明带）—— 使用合并K线坐标 x0/x1 水平定位
        def _pivot_xrange(p):
            x0 = p.get("x0"); x1 = p.get("x1")
            if x0 is None or x1 is None:
                # 回退：由笔/线段索引映射到合并K线坐标
                src = segments if p.get("from_segments") else strokes
                si, ei = p.get("start_idx"), p.get("end_idx")
                if src and si is not None and ei is not None and 0 <= si < len(src) and 0 <= ei < len(src):
                    a, b = src[si], src[ei]
                    x0 = a.get("start_idx", a.get("start", {}).get("index"))
                    x1 = b.get("end_idx", b.get("end", {}).get("index"))
            return x0, x1
        for p in used_pivots:
            x0, x1 = _pivot_xrange(p)
            if x0 is None or x1 is None or N <= 1:
                continue
            ax.axhspan(p["zd"], p["zg"], xmin=x0 / (N - 1), xmax=x1 / (N - 1),
                       color="#AB47BC", alpha=0.22, zorder=1)
            # 中枢上下沿虚线，便于看清震荡区间
            ax.plot([x0, x1], [p["zg"], p["zg"]], color="#AB47BC", linewidth=0.6, alpha=0.5, zorder=1)
            ax.plot([x0, x1], [p["zd"], p["zd"]], color="#AB47BC", linewidth=0.6, alpha=0.5, zorder=1)

        # 买卖点（箭头 + 名称标注；同方向末笔定位，纵向错开避免重叠）
        last_down = [s for s in strokes if s["direction"] == "down"]
        last_up = [s for s in strokes if s["direction"] == "up"]
        buy_off = sell_off = 0
        for tp in tps:
            if tp["type"] == "buy" and last_down:
                s = last_down[-1]; x = s["end"]["index"]; y = s["end"]["value"]
                yoff = y * 0.022 * buy_off; buy_off += 1
                ax.scatter([x], [y - yoff], marker="^", s=180, color=tp.get("color", "#FF4D4D"),
                           zorder=7, edgecolors="white", linewidths=0.7)
                ax.annotate(tp["name"], (x, y - yoff), color=tp.get("color", "#FF4D4D"),
                            fontsize=8, fontweight="bold", ha="center", va="top")
            elif tp["type"] == "sell" and last_up:
                s = last_up[-1]; x = s["end"]["index"]; y = s["end"]["value"]
                yoff = y * 0.022 * sell_off; sell_off += 1
                ax.scatter([x], [y + yoff], marker="v", s=180, color=tp.get("color", "#2ECC71"),
                           zorder=7, edgecolors="white", linewidths=0.7)
                ax.annotate(tp["name"], (x, y + yoff), color=tp.get("color", "#2ECC71"),
                            fontsize=8, fontweight="bold", ha="center", va="bottom")

        ax.set_title(f"{cfg['name'] if cfg else code} · 日线缠论结构（金=笔 蓝虚=线段 紫=中枢 红▲买 绿▼卖）",
                     color="#e0e0e0", fontsize=11, fontweight="bold")
        ax.tick_params(colors="#888888", labelsize=7)
        for spine in ax.spines.values():
            spine.set_color("#333333")

        # MACD 子图
        ax2 = fig.add_subplot(2, 1, 2)
        ax2.set_facecolor("#0d1117")
        dif, dea, hist = calc_macd(closes_m)
        if hist:
            ax2.bar(range(len(hist)), hist, width=0.8,
                    color=["#FF4D4D" if h >= 0 else "#2ECC71" for h in hist], alpha=0.85)
            ax2.plot(range(len(dif)), dif, color="#FFD700", linewidth=0.8)
            ax2.plot(range(len(dea)), dea, color="#4FC3F7", linewidth=0.8)
        ax2.set_title("MACD (12,26,9) — 背驰检测依据", color="#e0e0e0", fontsize=9)
        ax2.tick_params(colors="#888888", labelsize=7)
        for spine in ax2.spines.values():
            spine.set_color("#333333")

        fig.tight_layout()
        canvas = FigureCanvasTkAgg(fig, master=win)
        canvas.draw()
        canvas.get_tk_widget().pack(fill="both", expand=True)

    # ---- 数据加载与分析 ----

    def _load_kline_and_analyze(self):
        """后台加载K线数据并运行多级别缠论分析"""
        # 防止线程累积：检查是否有分析正在进行
        if hasattr(self, '_analyzing') and self._analyzing:
            if self.running:
                self.root.after(300000, self._load_kline_and_analyze)
            return
        self._analyzing = True
        
        def worker():
            try:
                count = 0
                # 缓存各品种的60分钟数据
                klines_60m_cache = {}
            
                for code in FETCH_ORDER:
                    if not self.running:
                        return
                    cfg = SYMBOLS[code]
                    kline_daily = None
                    kline_weekly = None
                    klines_60m = None
                    
                    if cfg["kline"]:
                        kline_daily = get_kline_data(code, scale=240, datalen=120)
                        # 周线从日线聚合（国际品种的API不支持直接获取周线）
                        if kline_daily:
                            kline_weekly = aggregate_daily_to_weekly(kline_daily)
                        # 小时级别K线
                        if code.startswith("sh") or code.startswith("sz"):
                            # A股用新浪获取60分钟K线
                            klines_60m = fetch_kline_minute_sina(code, scale=60, datalen=100)
                        elif code in ("au0", "ag0", "sc0"):
                            # 国内期货用akshare获取60分钟K线
                            if code not in klines_60m_cache:
                                klines_60m_cache[code] = fetch_kline_minute_akshare(period="60", symbol=code)
                            klines_60m = klines_60m_cache[code]
                        elif code.startswith("hf_"):
                            # 国际品种：使用本地积累的实时价格小时K线
                            klines_1h, klines_2h, klines_4h = get_accumulated_hourly_klines(code)
                            if klines_1h:
                                klines_60m = klines_1h  # 1h = 60min
                    else:
                        kline_daily = load_kline_cache(code, 240)
                    
                    if kline_daily:
                        # 多级别数据准备
                        timeframes_data = {}
                        
                        # 1h/2h/4h: 从60分钟聚合
                        if klines_60m and len(klines_60m) >= 30:
                            timeframes_data["1h"] = klines_60m  # 60min = 1h
                            timeframes_data["2h"] = aggregate_klines_60m(klines_60m, 2)
                            timeframes_data["4h"] = aggregate_klines_60m(klines_60m, 4)
                        
                        # 日线
                        timeframes_data["daily"] = kline_daily
                        
                        # 周线
                        if kline_weekly:
                            timeframes_data["weekly"] = kline_weekly
                        
                        # 月线: 从日线聚合
                        timeframes_data["monthly"] = aggregate_daily_to_monthly(kline_daily)
                        
                        # 对每个时间级别运行缠论分析
                        timeframe_results = {}
                        for tf_name, tf_data in timeframes_data.items():
                            # 降低阈值从20到15，确保更多级别参与分析
                            # analyze_chanlun内部要求>=15根K线
                            if tf_data and len(tf_data) >= 15:
                                result = analyze_chanlun(tf_data)
                                timeframe_results[tf_name] = result
                        
                        # 获取当前价格（用于多级别支撑阻力计算）
                        current_price = kline_daily[-1]["close"] if kline_daily else 0
                        
                        # 判断是否为国际品种（小时数据来自国内期货，价格单位不同）
                        is_international = code.startswith("hf_")
                        
                        # 多级别信号合成（缠论区间套）
                        synthesized = synthesize_multitimeframe_signals(timeframe_results, current_price, is_international)
                        
                        # 主结果使用合成信号，但保留日线分析的详细信息
                        daily_result = timeframe_results.get("daily", {})
                        if synthesized:
                            # 使用合成信号作为主信号
                            main_result = dict(daily_result)  # 复制日线结果
                            main_result["signal"] = synthesized["signal"]
                            main_result["signal_color"] = synthesized["signal_color"]
                            main_result["signal_desc"] = synthesized["signal_desc"]
                            main_result["synthesized_score"] = synthesized["score"]
                            main_result["synthesized_resonance"] = synthesized["resonance"]
                            # 使用多级别支撑阻力（如果有的话）
                            if synthesized.get("multi_support"):
                                main_result["nearest_support"] = synthesized["multi_support"]
                            if synthesized.get("multi_resistance"):
                                main_result["nearest_resistance"] = synthesized["multi_resistance"]
                        else:
                            main_result = daily_result
                        
                        # 添加多级别信息
                        main_result["timeframes"] = timeframe_results
                        main_result["timeframe_count"] = len(timeframe_results)
                        
                        # 综合各级别主力意图
                        main_result["mm_intention"] = synthesize_mm_intention(timeframe_results)
                        
                        # 计算预测
                        rt_data = self.rt_data.get(code, {})
                        current_price = rt_data.get("price", 0) if rt_data else 0
                        if current_price:
                            main_result["predictions"] = calc_prediction(main_result, current_price)
                        
                        # 保存日线原始K线，供K线图窗口绘制
                        main_result["klines_daily"] = kline_daily
                        self.analysis[code] = main_result
                        count += 1
                
                # 多品种联立分析
                group_results = analyze_commodity_groups(self.analysis)
                self.group_results = group_results
                
                # 定期保存小时级别缓存到磁盘
                for code in FETCH_ORDER:
                    if code.startswith("hf_"):
                        save_hourly_cache(code)
                
                self.kline_loaded = True
                tf_info = "多级别联立" if count > 0 else ""
                group_info = "、".join([f"{k}:{v['signal']}" for k, v in group_results.items()])
                self.root.after(0, lambda: self._on_analysis_complete(count, tf_info, group_info))
            except Exception as e:
                print(f"[分析异常] {e}")
            finally:
                self._analyzing = False
            # 每5分钟重新分析
            if self.running:
                self.root.after(300000, self._load_kline_and_analyze)
        threading.Thread(target=worker, daemon=True).start()

    def _start_rt_loop(self):
        """实时行情更新循环"""
        def update():
            if not self.running:
                return
            a_syms = [s for s in FETCH_ORDER if s.startswith("sh") or s.startswith("sz")]
            g_syms = [s.replace("hf_", "") for s in FETCH_ORDER if s.startswith("hf")]
            f_syms = [s for s in FETCH_ORDER if s in ("au0", "ag0", "sc0")]
            data = {}
            if a_syms:
                rt = fetch_realtime(a_syms)
                if rt:
                    data.update(rt)
            if g_syms:
                rt = fetch_realtime([f"hf_{s}" for s in g_syms])
                if rt:
                    data.update(rt)
            if f_syms:
                rt = fetch_realtime_futures(f_syms)
                if rt:
                    data.update(rt)
            if data:
                self.rt_data = data
                # 为国际品种积累K线（异常保护）
                for code in FETCH_ORDER:
                    if code.startswith("hf_") and code in data:
                        d = data[code]
                        try:
                            # 确保价格有效（high/low/open 为0时用price填充）
                            price = d.get("price", 0)
                            high = d.get("high", 0) or price
                            low = d.get("low", 0) or price
                            open_p = d.get("open", 0) or price
                            if price > 0:
                                accumulate_gold_kline(price, high, low, open_p, symbol=code)
                                # 同时积累小时级别K线
                                accumulate_hourly_kline(price, symbol=code)
                        except Exception as e:
                            print(f"[积累K线异常] {code}: {e}")
                for code in FETCH_ORDER:
                    if code in data:
                        self._update_row(code, data[code])
                self.time_label.config(text=time.strftime("%H:%M:%S"))
            self.root.after(5000, update)
        update()

    def _update_row(self, code, data):
        if code not in self.rows:
            return
        row = self.rows[code]
        price = data["price"]
        pct = data.get("pct", 0)
        row["price"].config(text=fmt_price(price))
        if pct > 0:
            row["pct"].config(text=f"+{pct:.2f}%", fg=self.UP)
        elif pct < 0:
            row["pct"].config(text=f"{pct:.2f}%", fg=self.DOWN)
        else:
            row["pct"].config(text="0.00%", fg=self.FLAT)
        # 缠论信号
        ana = self.analysis.get(code)
        if ana:
            row["signal"].config(text=ana["signal"], bg=ana["signal_color"],
                                 fg=self._contrast(ana["signal_color"]))
            # 主力意图
            mm_list = ana.get("mm_intention", [])
            if mm_list:
                mm = mm_list[0]  # 显示置信度最高的意图
                icon = mm.get("icon", "")
                mm_type = mm.get("type", "")
                mm_color = mm.get("color", self.FG_DIM)
                row["mm_intention"].config(text=f"{icon}{mm_type}", fg=mm_color)
            else:
                row["mm_intention"].config(text="--", fg=self.FG_DIM)
            # 中枢数量
            pivot_count = len(ana.get("all_pivots_info", []))
            row["pivot"].config(text=f"{pivot_count}个", fg="#87CEEB")
            # 支撑位
            if ana["nearest_support"]:
                row["support"].config(text=f"↓{ana['nearest_support']:.2f}", fg=self.DOWN)
            else:
                row["support"].config(text="--", fg=self.FG_DIM)
            # 阻力位
            if ana["nearest_resistance"]:
                row["resistance"].config(text=f"↑{ana['nearest_resistance']:.2f}", fg=self.UP)
            else:
                row["resistance"].config(text="--", fg=self.FG_DIM)
            # 预测（4小时+日线，分别显示各自颜色）
            predictions = ana.get("predictions", {})
            if predictions:
                hourly_pred = predictions.get("4h", predictions.get("2h", predictions.get("1h", {})))
                daily_pred = predictions.get("daily", {})
                # 小时级别预测
                if hourly_pred:
                    h_dir = hourly_pred.get("direction", "↔震荡")
                    h_target = hourly_pred.get("target", 0)
                    h_stop = hourly_pred.get("stop_loss", 0)
                    # 根据价格大小选择格式
                    fmt = ".1f" if h_target >= 100 else ".2f"
                    # 显示：方向+目标+止损
                    if "涨" in h_dir:
                        row["pred_h"].config(text=f"H↑{h_target:{fmt}} 止{h_stop:{fmt}}")
                        row["pred_h"].config(fg=self.UP)
                    elif "跌" in h_dir:
                        row["pred_h"].config(text=f"H↓{h_target:{fmt}} 止{h_stop:{fmt}}")
                        row["pred_h"].config(fg=self.DOWN)
                    else:
                        row["pred_h"].config(text=f"H↔{h_target:{fmt}} 止{h_stop:{fmt}}")
                        row["pred_h"].config(fg=self.FLAT)
                else:
                    row["pred_h"].config(text="--", fg=self.FG_DIM)
                # 日线级别预测
                if daily_pred:
                    d_dir = daily_pred.get("direction", "↔震荡")
                    d_target = daily_pred.get("target", 0)
                    d_stop = daily_pred.get("stop_loss", 0)
                    # 根据价格大小选择格式
                    fmt = ".1f" if d_target >= 100 else ".2f"
                    # 显示：方向+目标+止损
                    if "涨" in d_dir:
                        row["pred_d"].config(text=f"D↑{d_target:{fmt}} 止{d_stop:{fmt}}")
                        row["pred_d"].config(fg=self.UP)
                    elif "跌" in d_dir:
                        row["pred_d"].config(text=f"D↓{d_target:{fmt}} 止{d_stop:{fmt}}")
                        row["pred_d"].config(fg=self.DOWN)
                    else:
                        row["pred_d"].config(text=f"D↔{d_target:{fmt}} 止{d_stop:{fmt}}")
                        row["pred_d"].config(fg=self.FLAT)
                else:
                    row["pred_d"].config(text="--", fg=self.FG_DIM)
            else:
                row["pred_h"].config(text="--", fg=self.FG_DIM)
                row["pred_d"].config(text="--", fg=self.FG_DIM)
            
            # 更新仓位建议（自动交易模式）
            self._update_single_position_advice(code)
        else:
            row["signal"].config(text="计算中...", bg=self.FG_DIM, fg=self._contrast(self.FG_DIM))
            row["mm_intention"].config(text="--", fg=self.FG_DIM)
            row["pivot"].config(text="--")
            row["support"].config(text="--")
            row["resistance"].config(text="--")
            row["pred_h"].config(text="--", fg=self.FG_DIM)
            row["pred_d"].config(text="--", fg=self.FG_DIM)
            row["pos_cost"].config(text="--", fg=self.FG_DIM)
            row["pos_value"].config(text="--", fg=self.FG_DIM)
            row["pos_action"].config(text="--", fg=self.FG_DIM)

    # ---- Tooltip ----

    def _show_tooltip(self, code):
        self._close_tooltip()
        cfg = SYMBOLS.get(code)
        data = self.rt_data.get(code)
        ana = self.analysis.get(code)
        if not cfg or not data:
            return
        price = data["price"]
        signal = ana["signal"] if ana else "●观望"
        color = ana["signal_color"] if ana else "#888888"
        desc = ana["signal_desc"] if ana else "K线数据加载中..."

        tip = tk.Toplevel(self.root)
        tip.overrideredirect(True)
        tip.attributes("-topmost", True)
        tip.attributes("-alpha", 0.95)
        row_w = self.rows[code]["row"]
        tip_w = 480
        x = row_w.winfo_rootx() - tip_w + self.width + 10
        y = row_w.winfo_rooty() + row_w.winfo_height() + 2
        sh = self.root.winfo_screenheight()
        # 先不设置高度，让内容决定
        tip.geometry(f"{tip_w}x1+{x}+{y}")

        frame = tk.Frame(tip, bg="#1a1a2e", relief="solid", bd=1,
                         highlightbackground=color, highlightthickness=2)
        frame.pack(fill="both", expand=True)
        # 标题
        tf = tk.Frame(frame, bg=color); tf.pack(fill="x")
        tk.Label(tf, text=f"  {cfg['name']} ({cfg['symbol']})", bg=color, fg="white",
                 font=("Microsoft YaHei", 10, "bold"), anchor="w").pack(side="left", padx=4, pady=4)
        tk.Label(tf, text=f" {signal} ", bg=color, fg="white",
                 font=("Microsoft YaHei", 10, "bold"), anchor="e").pack(side="right", padx=4)
        # 价格
        info = tk.Frame(frame, bg="#1a1a2e"); info.pack(fill="x", padx=8, pady=(6,2))
        pct_t = f"{'+' if data.get('pct',0)>=0 else ''}{data.get('pct',0):.2f}%"
        pct_c = self.UP if data.get("pct",0) > 0 else self.DOWN if data.get("pct",0) < 0 else self.FLAT
        tk.Label(info, text=f"最新价: {price:.2f} {cfg['unit']}", bg="#1a1a2e", fg=self.FG,
                 font=("Consolas", 10, "bold"), anchor="w").pack(fill="x")
        tk.Label(info, text=f"涨跌幅: {pct_t}  |  高:{data.get('high',0):.2f}  低:{data.get('low',0):.2f}",
                 bg="#1a1a2e", fg=pct_c, font=("Consolas", 8), anchor="w").pack(fill="x")
        tk.Frame(frame, bg="#333", height=1).pack(fill="x", padx=8, pady=4)
        # 多级别分析汇总（信号/趋势/支撑阻力/预测 按时间级别一一对应）
        if ana and ana.get("timeframes"):
            tf_frame = tk.Frame(frame, bg="#1a1a2e"); tf_frame.pack(fill="x", padx=8, pady=2)
            tk.Label(tf_frame, text="📊 多级别分析:", bg="#1a1a2e", fg="#FFD700",
                     font=("Microsoft YaHei", 8, "bold"), anchor="w").pack(fill="x")
            tf_names = {"1h": "1小时", "2h": "2小时", "4h": "4小时", 
                       "daily": "日线", "weekly": "周线", "monthly": "月线"}
            trend_map = {"up": "↑上涨", "down": "↓下跌", "consolidation": "↔震荡", "unknown": "?未知"}
            trend_colors = {"up": "#FF4444", "down": "#44AA44", "consolidation": "#888888", "unknown": "#666666"}
            predictions = ana.get("predictions", {})
            # 从大级别到小级别显示
            for tf_key in ["monthly", "weekly", "daily", "4h", "2h", "1h"]:
                tf_result = ana["timeframes"].get(tf_key)
                if not tf_result:
                    continue
                tf_signal = tf_result.get("signal", "●观望")
                tf_sig_color = tf_result.get("signal_color", "#888888")
                tf_trend = tf_result.get("trend", "unknown")
                tf_support = tf_result.get("nearest_support")
                tf_resistance = tf_result.get("nearest_resistance")
                pred = predictions.get(tf_key)
                # 第一行：级别名 + 信号 + 趋势
                line1 = f"  【{tf_names[tf_key]}】 {tf_signal}  {trend_map.get(tf_trend, '?')}"
                lbl1 = tk.Label(tf_frame, text=line1, bg="#1a1a2e", fg=tf_sig_color,
                               font=("Microsoft YaHei", 7, "bold"), anchor="w")
                lbl1.pack(fill="x")
                # 第二行：支撑阻力 + 预测
                sr_text = ""
                if tf_support:
                    sr_text += f"↓支撑:{tf_support:.2f} "
                if tf_resistance:
                    sr_text += f"↑阻力:{tf_resistance:.2f}"
                if not sr_text:
                    sr_text = "无中枢价位"
                pred_text = ""
                if pred:
                    pred_text = f"  预测:{pred['direction']} 目标:{pred['target']:.2f}"
                line2 = f"    {sr_text}{pred_text}"
                # 预测颜色
                if pred and ("涨" in pred.get("direction", "") or "多" in pred.get("direction", "")):
                    pred_color = "#FF4444"
                elif pred and ("跌" in pred.get("direction", "") or "空" in pred.get("direction", "")):
                    pred_color = "#44AA44"
                else:
                    pred_color = "#888888"
                lbl2 = tk.Label(tf_frame, text=line2, bg="#1a1a2e", fg=pred_color,
                               font=("Microsoft YaHei", 7), anchor="w")
                lbl2.pack(fill="x")
                # 第三行：主力意图
                mm_list = tf_result.get("mm_intention", [])
                if mm_list and isinstance(mm_list, list):
                    mm_text = "    "
                    valid_mm_count = 0
                    first_mm_color = "#666666"
                    for mm in mm_list:
                        if not isinstance(mm, dict):
                            continue
                        if valid_mm_count == 0:
                            first_mm_color = mm.get("color", "#666666")
                        icon = mm.get("icon", "")
                        mm_type = mm.get("type", "")
                        conf = mm.get("confidence", 0)
                        conf_bar = "■" * int(conf * 5) + "□" * (5 - int(conf * 5))
                        mm_text += f"{icon}{mm_type}[{conf_bar}] "
                        valid_mm_count += 1
                    mm_color = first_mm_color
                    if valid_mm_count > 0:
                        lbl3 = tk.Label(tf_frame, text=mm_text.rstrip(), bg="#1a1a2e", fg=mm_color,
                                       font=("Microsoft YaHei", 7), anchor="w")
                        lbl3.pack(fill="x")
                    else:
                        lbl3 = tk.Label(tf_frame, text="    ➖自然走势", bg="#1a1a2e", fg="#666666",
                                       font=("Microsoft YaHei", 7), anchor="w")
                        lbl3.pack(fill="x")
                else:
                    lbl3 = tk.Label(tf_frame, text="    ➖自然走势", bg="#1a1a2e", fg="#666666",
                                   font=("Microsoft YaHei", 7), anchor="w")
                    lbl3.pack(fill="x")
            tk.Frame(frame, bg="#333", height=1).pack(fill="x", padx=8, pady=4)
        # 缠论结构多级别摘要（笔 / 线段 / 中枢 / 背驰 / 买卖点 / 强度）
        if ana:
            sf2 = tk.Frame(frame, bg="#1a1a2e"); sf2.pack(fill="x", padx=8, pady=2)
            tk.Label(sf2, text="🧩 缠论结构(日线):", bg="#1a1a2e", fg="#FFD700",
                     font=("Microsoft YaHei", 8, "bold"), anchor="w").pack(fill="x")
            daily_res = ana.get("timeframes", {}).get("daily", ana)
            n_strokes = len(daily_res.get("strokes", []))
            n_segs = len(daily_res.get("segments", []))
            pivs = daily_res.get("std_pivots") or daily_res.get("pivots") or []
            n_piv = len(pivs)
            tk.Label(sf2, text=f"  笔 {n_strokes} 根 · 线段 {n_segs} 段 · 中枢 {n_piv} 个",
                     bg="#1a1a2e", fg="#87CEEB", font=("Microsoft YaHei", 8), anchor="w").pack(fill="x")
            # 背驰信息
            div = daily_res.get("divergence_info") or {}
            if div and div.get("type") and div.get("type") != "none":
                kind = div.get("kind", "consolidation")
                ratio = div.get("ratio", 1.0)
                div_color = "#FF4444" if div["type"] == "bottom" else "#44AA44"
                div_text = f"  背驰: {'底' if div['type'] == 'bottom' else '顶'}背驰 · {kind} · MACD面积比{ratio:.2f}"
                tk.Label(sf2, text=div_text, bg="#1a1a2e", fg=div_color,
                         font=("Microsoft YaHei", 8, "bold"), anchor="w").pack(fill="x")
            else:
                tk.Label(sf2, text="  背驰: 暂无明显背驰", bg="#1a1a2e", fg="#666666",
                         font=("Microsoft YaHei", 7), anchor="w").pack(fill="x")
            # 买卖点
            tps = daily_res.get("trading_points", [])
            if tps:
                names = "  ".join(p.get("name", "") for p in tps)
                tk.Label(sf2, text=f"  买卖点: {names}", bg="#1a1a2e", fg="#FFD700",
                         font=("Microsoft YaHei", 8, "bold"), anchor="w").pack(fill="x")
            # 信号强度条（买卖点强度均值，或合成评分折算；0~100%）
            strength = 0.0
            if tps:
                strength = sum(p.get("strength", 0) for p in tps) / len(tps)
            elif ana.get("synthesized_score") is not None:
                strength = min(1.0, abs(ana["synthesized_score"]) / 3.0)
            bar_full = "█" * int(strength * 20)
            bar_empty = "░" * (20 - int(strength * 20))
            s_color = ana.get("signal_color", "#888888")
            tk.Label(sf2, text=f"  信号强度: [{bar_full}{bar_empty}] {strength*100:.0f}%",
                     bg="#1a1a2e", fg=s_color, font=("Consolas", 8), anchor="w").pack(fill="x")
            tk.Frame(frame, bg="#333", height=1).pack(fill="x", padx=8, pady=4)
        # 小时K线积累状态（仅国际品种）
        if code.startswith("hf_"):
            # 直接获取积累数据（不限制最小条数）
            cache_key = f"{code}_1h"
            history = _hourly_cache.get(cache_key, [])
            current = _hourly_current.get(cache_key)
            hour_count = len(history) + (1 if current else 0)
            
            if hour_count > 0:
                min_required = 30
                if hour_count < min_required:
                    status_color = "#FF6B35" if hour_count >= 10 else "#FF4444"
                    tk.Label(frame, text=f"⏳ 小时K线积累: {hour_count}/{min_required}条 (还需{min_required-hour_count}条)",
                             bg="#1a1a2e", fg=status_color, font=("Microsoft YaHei", 8), anchor="w").pack(fill="x", padx=8, pady=2)
                else:
                    tk.Label(frame, text=f"✓ 小时K线: {hour_count}条 (已就绪)",
                             bg="#1a1a2e", fg="#4CAF50", font=("Microsoft YaHei", 8), anchor="w").pack(fill="x", padx=8, pady=2)
            else:
                tk.Label(frame, text="⏳ 小时K线积累中... (需要至少30条)",
                         bg="#1a1a2e", fg="#FF4444", font=("Microsoft YaHei", 8), anchor="w").pack(fill="x", padx=8, pady=2)
        # 综合评分与共振
        if ana:
            lf = tk.Frame(frame, bg="#1a1a2e"); lf.pack(fill="x", padx=8, pady=2)
            # 分析级别
            tf_count = ana.get("timeframe_count", 1)
            tk.Label(lf, text=f"  级别: 共{tf_count}个级别联立分析", bg="#1a1a2e", fg="#87CEEB",
                     font=("Microsoft YaHei", 8), anchor="w").pack(fill="x")
            # 多级别合成评分
            syn_score = ana.get("synthesized_score")
            if syn_score is not None:
                score_color = "#FF4444" if syn_score > 0.5 else "#44AA44" if syn_score < -0.5 else "#888888"
                tk.Label(lf, text=f"  综合评分: {syn_score:.2f} (范围 -3 ~ +3)",
                         bg="#1a1a2e", fg=score_color, font=("Microsoft YaHei", 8, "bold"), anchor="w").pack(fill="x")
            resonance = ana.get("synthesized_resonance")
            if resonance:
                tk.Label(lf, text=f"  ⚡ {resonance}",
                         bg="#1a1a2e", fg="#FFD700", font=("Microsoft YaHei", 8, "bold"), anchor="w").pack(fill="x")
        tk.Frame(frame, bg="#333", height=1).pack(fill="x", padx=8, pady=4)
        # 主力意图详解（综合各级别）
        if ana:
            mm_list = ana.get("mm_intention", [])
            if mm_list and isinstance(mm_list, list):
                mm_frame = tk.Frame(frame, bg="#1a1a2e"); mm_frame.pack(fill="x", padx=8, pady=2)
                tk.Label(mm_frame, text="🕵️ 主力意图(综合):", bg="#1a1a2e", fg="#FFD700",
                         font=("Microsoft YaHei", 8, "bold"), anchor="w").pack(fill="x")
                for mm in mm_list:
                    if not isinstance(mm, dict):
                        continue
                    icon = mm.get("icon", "")
                    mm_type = mm.get("type", "")
                    conf = mm.get("confidence", 0)
                    mm_desc = mm.get("desc", "")
                    mm_color = mm.get("color", "#888888")
                    sources = mm.get("sources", [])
                    conf_pct = f"{conf*100:.0f}%"
                    src_text = f" [{','.join(sources)}级别]" if sources else ""
                    tk.Label(mm_frame, text=f"  {icon} {mm_type} (置信度:{conf_pct}){src_text}",
                             bg="#1a1a2e", fg=mm_color,
                             font=("Microsoft YaHei", 8, "bold"), anchor="w").pack(fill="x")
                    tk.Label(mm_frame, text=f"     {mm_desc}",
                             bg="#1a1a2e", fg=self.FG,
                             font=("Microsoft YaHei", 7), anchor="w",
                             wraplength=420, justify="left").pack(fill="x")
        tk.Frame(frame, bg="#333", height=1).pack(fill="x", padx=8, pady=4)
        sf = tk.Frame(frame, bg="#1a1a2e"); sf.pack(fill="x", padx=4, pady=2)
        tk.Label(sf, text=f"📍 当前信号: {signal}", bg="#1a1a2e", fg=color,
                 font=("Microsoft YaHei", 9, "bold"), anchor="w").pack(fill="x", padx=4)
        # 信号说明
        tk.Label(sf, text=desc, bg="#1a1a2e", fg=self.FG, font=("Microsoft YaHei", 9),
                 anchor="nw", justify="left", wraplength=440).pack(fill="x", padx=4, pady=2)
        # 多级别中枢列表
        if ana and ana.get("timeframes"):
            tk.Frame(sf, bg="#333", height=1).pack(fill="x", padx=4, pady=6)
            tk.Label(sf, text="📋 多级别中枢:", bg="#1a1a2e", fg="#FFD700",
                     font=("Microsoft YaHei", 8, "bold"), anchor="w").pack(fill="x", padx=4)
            tf_names_full = {"1h": "1小时", "2h": "2小时", "4h": "4小时", "daily": "日线", "weekly": "周线", "monthly": "月线"}
            for tf_key in ["monthly", "weekly", "daily", "4h", "2h", "1h"]:
                tf_result = ana["timeframes"].get(tf_key)
                if tf_result and tf_result.get("all_pivots_info"):
                    tf_name = tf_names_full.get(tf_key, tf_key)
                    pivot_count = len(tf_result["all_pivots_info"])
                    tk.Label(sf, text=f"  【{tf_name}】{pivot_count}个中枢:", bg="#1a1a2e", fg="#87CEEB",
                             font=("Microsoft YaHei", 7, "bold"), anchor="w").pack(fill="x", padx=4)
                    for pi in tf_result["all_pivots_info"][:3]:  # 每个级别最多显示3个中枢
                        tk.Label(sf, text=f"    {pi}", bg="#1a1a2e", fg="#e0e0e0",
                                 font=("Microsoft YaHei", 7), anchor="w").pack(fill="x", padx=4)
        elif ana and ana.get("all_pivots_info"):
            # 如果没有多级别数据，只显示日线中枢
            tk.Frame(sf, bg="#333", height=1).pack(fill="x", padx=4, pady=6)
            tk.Label(sf, text="📋 日线中枢:", bg="#1a1a2e", fg="#FFD700",
                     font=("Microsoft YaHei", 8, "bold"), anchor="w").pack(fill="x", padx=4)
            for pi in ana["all_pivots_info"]:
                tk.Label(sf, text=f"  {pi}", bg="#1a1a2e", fg="#e0e0e0",
                         font=("Microsoft YaHei", 8), anchor="w").pack(fill="x", padx=4)
        
        # 仓位操作建议
        advice = self.position_advice.get(code)
        if advice:
            tk.Frame(frame, bg="#333", height=1).pack(fill="x", padx=8, pady=4)
            af = tk.Frame(frame, bg="#1a1a2e"); af.pack(fill="x", padx=4, pady=2)
            
            action = advice.get("action", "持有")
            amount = advice.get("amount", 0)
            color = advice.get("color", "#888888")
            target_pos = advice.get("target_position", 0)
            stop_loss = advice.get("stop_loss")
            take_profit = advice.get("take_profit")
            detail = advice.get("advice_detail", "")
            pnl_pct = advice.get("pnl_pct", 0)
            pnl_amount = advice.get("pnl_amount", 0)
            pos_data = self.positions.get(code, {})
            cost_price = pos_data.get("cost_price", 0)
            position_value = pos_data.get("position_value", 0)
            
            # 标题
            tk.Label(af, text="💰 仓位操作建议:", bg="#1a1a2e", fg="#FFD700",
                     font=("Microsoft YaHei", 8, "bold"), anchor="w").pack(fill="x", padx=4)
            
            # 盈亏状态（有持仓时显示）
            if cost_price > 0 and position_value > 0:
                pnl_color = self.UP if pnl_pct >= 0 else self.DOWN
                pnl_icon = "📈" if pnl_pct >= 0 else "📉"
                pnl_sign = "+" if pnl_pct >= 0 else ""
                pnl_text = f"  {pnl_icon} 盈亏: {pnl_sign}{pnl_pct:.1f}%  ({pnl_sign}{pnl_amount/10000:.2f}万)"
                tk.Label(af, text=pnl_text, bg="#1a1a2e", fg=pnl_color,
                         font=("Microsoft YaHei", 9, "bold"), anchor="w").pack(fill="x", padx=4)
                # 成本/现价
                tk.Label(af, text=f"     成本: {fmt_price(cost_price)}  现价: {fmt_price(price)}",
                         bg="#1a1a2e", fg=self.FG_DIM,
                         font=("Microsoft YaHei", 7), anchor="w").pack(fill="x", padx=4)
            
            # 操作 + 金额
            if amount > 0:
                action_text = f"  \u25b6 {action} {amount/10000:.1f}\u4e07 \u2192 \u76ee\u6807\u4ed3\u4f4d{target_pos:.0f}%"
            else:
                action_text = f"  \u25b6 {action} (\u5f53\u524d\u4ed3\u4f4d{target_pos:.0f}%)"
            tk.Label(af, text=action_text, bg="#1a1a2e", fg=color,
                     font=("Microsoft YaHei", 9, "bold"), anchor="w").pack(fill="x", padx=4)
            
            # 详细策略（包含止损止盈）
            if detail:
                tk.Label(af, text=f"     {detail}", bg="#1a1a2e", fg=self.FG,
                         font=("Microsoft YaHei", 7), anchor="w",
                         wraplength=420, justify="left").pack(fill="x", padx=4)
            
            # 止损止盈价位
            if stop_loss and stop_loss > 0:
                sl_dist = (price - stop_loss) / price * 100 if price > 0 else 0
                tk.Label(af, text=f"     \U0001f6e1\ufe0f 止损: {stop_loss:.2f} (距现价-{sl_dist:.1f}%)",
                         bg="#1a1a2e", fg="#FF6B6B",
                         font=("Microsoft YaHei", 7), anchor="w").pack(fill="x", padx=4)
            if take_profit and take_profit > 0:
                tp_dist = (take_profit - price) / price * 100 if price > 0 else 0
                tk.Label(af, text=f"     \U0001f3af 止盈: {take_profit:.2f} (距现价+{tp_dist:.1f}%)",
                         bg="#1a1a2e", fg="#4CAF50",
                         font=("Microsoft YaHei", 7), anchor="w").pack(fill="x", padx=4)
        
        # 底部
        tk.Label(frame, text="点击空白处关闭 | 缠论分析仅供参考",
                 bg="#1a1a2e", fg=self.FG_DIM, font=("Microsoft YaHei", 7)).pack(pady=4)
        # 自适应高度：更新后获取实际高度，重新设置geometry
        tip.update_idletasks()
        actual_h = tip.winfo_reqheight()
        # 检查是否超出屏幕底部
        if y + actual_h > sh:
            y = row_w.winfo_rooty() - actual_h - 5
        # 检查是否超出屏幕上沿
        if y < 0:
            y = 5
        if x < 0:
            x = 10
        tip.geometry(f"{tip_w}x{actual_h}+{x}+{y}")
        # 关闭
        tip.bind("<Button-1>", lambda e: self._close_tooltip())
        for w in tip.winfo_children():
            w.bind("<Button-1>", lambda e: self._close_tooltip())
            for c in w.winfo_children():
                c.bind("<Button-1>", lambda e: self._close_tooltip())
        self.tooltip_window = tip

    def _close_tooltip(self):
        if self.tooltip_window:
            try:
                self.tooltip_window.unbind_all("<MouseWheel>")
                self.tooltip_window.destroy()
            except Exception:
                pass
            self.tooltip_window = None

    # ---- 窗口控制 ----

    def _start_drag(self, event):
        self.drag_data = {"x": event.x, "y": event.y}
    def _do_drag(self, event):
        self.root.geometry(f"+{self.root.winfo_x()+event.x-self.drag_data['x']}+"
                           f"{self.root.winfo_y()+event.y-self.drag_data['y']}")
    def _minimize(self):
        """自定义最小化按钮"""
        if not self._minimized:
            # 最小化：先保存当前位置（在改变geometry之前）
            self.root.update_idletasks()
            self._saved_pos = (self.root.winfo_x(), self.root.winfo_y())
            self._minimized = True
            # 隐藏主内容和底部元素
            self.data_frame.pack_forget()
            children = self.main_frame.winfo_children()
            for w in children:
                if w != children[0]:  # 保留标题栏
                    w.pack_forget()
            # 只改变高度，不改变位置
            self.root.geometry(f"{self.width}x32+{self._saved_pos[0]}+{self._saved_pos[1]}")
        else:
            # 恢复
            self._minimized = False
            # 使用保存的位置
            if self._saved_pos:
                x, y = self._saved_pos
            else:
                # 如果没有保存位置，使用屏幕右上角
                sx = self.root.winfo_screenwidth()
                x = sx - self.width - 20
                y = 30
            self.root.geometry(f"{self.width}x{self.height}+{x}+{y}")
            self.root.update_idletasks()
            # 重建UI并刷新数据
            self.rows = {}
            self._build_ui()
            self.root.after(100, self._refresh_all_data)

    def _on_minimize_taskbar(self, event):
        """任务栏最小化时保存位置"""
        if not self._minimized:
            self.root.update_idletasks()
            self._saved_pos = (self.root.winfo_x(), self.root.winfo_y())

    def _on_restore(self, event):
        """任务栏恢复时重建UI"""
        if self._minimized:
            self._minimized = False
            # 使用保存的位置
            if self._saved_pos:
                x, y = self._saved_pos
            else:
                sx = self.root.winfo_screenwidth()
                x = sx - self.width - 20
                y = 30
            self.root.geometry(f"{self.width}x{self.height}+{x}+{y}")
            # 延迟重建UI，等待窗口完全恢复
            self.root.after(100, self._rebuild_ui)

    def _rebuild_ui(self):
        """重建UI并刷新数据"""
        self.rows = {}
        self._build_ui()
        # 重建后立即刷新数据（增加延迟确保UI完全就绪）
        self.root.after(100, self._refresh_all_data)
    
    def _refresh_all_data(self):
        """用内存中的数据刷新所有行显示"""
        # 刷新实时行情和分析数据
        if self.rt_data:
            for code in FETCH_ORDER:
                if code in self.rt_data and code in self.rows:
                    self._update_row(code, self.rt_data[code])
        # 刷新多品种联立分析
        if hasattr(self, 'group_results') and self.group_results:
            self._update_group_display(self.group_results)
        # 如果没有分析数据，触发重新分析
        if not self.analysis or len(self.analysis) == 0:
            self.kline_loaded = False
            self._analyzing = False
            self._load_kline_and_analyze()
    def _toggle_pin(self):
        cur = self.root.attributes("-topmost")
        self.root.attributes("-topmost", not cur)
        self.pin_btn.config(fg="#FFD700" if not cur else self.FG_DIM)
    def _close(self):
        self.running = False
        self._close_tooltip()
        self.root.destroy()
    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    app = GoldMonitor()
    app.run()
