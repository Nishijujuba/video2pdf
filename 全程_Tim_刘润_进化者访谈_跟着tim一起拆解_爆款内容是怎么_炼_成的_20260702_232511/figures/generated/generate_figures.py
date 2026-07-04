from pathlib import Path

import matplotlib as mpl
import matplotlib.pyplot as plt
from matplotlib.font_manager import FontProperties
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch, Rectangle


OUT_DIR = Path(__file__).resolve().parent
FONT_PATH = Path(r"C:\Windows\Fonts\NotoSansSC-VF.ttf")
FONT = FontProperties(fname=str(FONT_PATH))

mpl.rcParams["pdf.fonttype"] = 42
mpl.rcParams["ps.fonttype"] = 42
mpl.rcParams["axes.unicode_minus"] = False

COLORS = {
    "ink": "#1f2933",
    "muted": "#6b7280",
    "line": "#94a3b8",
    "blue": "#2563eb",
    "cyan": "#0891b2",
    "green": "#16a34a",
    "amber": "#d97706",
    "red": "#dc2626",
    "violet": "#7c3aed",
    "bg": "#f8fafc",
    "panel": "#ffffff",
}


def setup(name, size=(11, 7)):
    fig, ax = plt.subplots(figsize=size)
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")
    ax.add_patch(Rectangle((0, 0), 1, 1, facecolor=COLORS["bg"], edgecolor="none", zorder=0))
    return fig, ax


def text(ax, x, y, s, size=13, color=None, ha="center", va="center", weight="regular", linespacing=1.25):
    ax.text(
        x,
        y,
        s,
        fontproperties=FONT,
        fontsize=size,
        color=color or COLORS["ink"],
        ha=ha,
        va=va,
        fontweight=weight,
        linespacing=linespacing,
        zorder=3,
    )


def box(ax, xy, w, h, label, fc="#ffffff", ec=None, lw=1.4, radius=0.025, size=12, color=None):
    patch = FancyBboxPatch(
        xy,
        w,
        h,
        boxstyle=f"round,pad=0.012,rounding_size={radius}",
        facecolor=fc,
        edgecolor=ec or COLORS["line"],
        linewidth=lw,
        zorder=2,
    )
    ax.add_patch(patch)
    text(ax, xy[0] + w / 2, xy[1] + h / 2, label, size=size, color=color)
    return patch


def arrow(ax, start, end, color=None, lw=1.8, rad=0.0):
    ax.add_patch(
        FancyArrowPatch(
            start,
            end,
            arrowstyle="-|>",
            mutation_scale=14,
            linewidth=lw,
            color=color or COLORS["line"],
            connectionstyle=f"arc3,rad={rad}",
            zorder=1,
        )
    )


def save(fig, name):
    fig.savefig(OUT_DIR / f"{name}.pdf", bbox_inches="tight", facecolor=fig.get_facecolor())
    fig.savefig(OUT_DIR / f"{name}.png", dpi=220, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)


def burst_four_traits():
    fig, ax = setup("burst_four_traits")
    text(ax, 0.5, 0.93, "爆款内容四特质模型", size=23, weight="bold")
    text(ax, 0.5, 0.875, "传播欲来自四种可检验特质的合成结果", size=12, color=COLORS["muted"])

    traits = [
        ((0.10, 0.56), "H 快乐 / 幽默\n天然分享给亲友", COLORS["amber"]),
        ((0.58, 0.56), "K 知识\n有用、可转述、能帮人", COLORS["blue"]),
        ((0.10, 0.24), "R 共鸣\n真实经历触发情绪连接", COLORS["green"]),
        ((0.58, 0.24), "R 节奏\n铺垫、反应、收束有控制", COLORS["violet"]),
    ]
    for xy, label, color in traits:
        box(ax, xy, 0.32, 0.18, label, fc="#ffffff", ec=color, lw=2.0, size=13)
        arrow(ax, (xy[0] + 0.16, xy[1] + 0.09), (0.50, 0.48), color=color, lw=1.5)

    box(ax, (0.38, 0.405), 0.24, 0.15, "传播欲\n愿意发给别人", fc="#eff6ff", ec=COLORS["blue"], lw=2.2, size=15)
    text(ax, 0.5, 0.13, "使用方式：每个选题都拿四个问题拷问自己；超级爆款通常四项同时成立。", size=12, color=COLORS["muted"])
    save(fig, "fig_burst_four_traits_model")


def creator_flywheel():
    fig, ax = setup("creator_flywheel")
    text(ax, 0.5, 0.93, "创作飞轮：从单条作品到方法沉淀", size=22, weight="bold")
    text(ax, 0.5, 0.875, "飞轮强调循环增益：每轮反馈都应该提高下一轮选题和执行质量", size=12, color=COLORS["muted"])

    nodes = [
        ((0.40, 0.70), "选题假设\n要解决什么传播问题"),
        ((0.67, 0.58), "高质量投入\n创意、预算、时间"),
        ((0.67, 0.30), "发布与传播\n播放、转发、共鸣"),
        ((0.40, 0.18), "复盘反馈\n哪些机制有效"),
        ((0.13, 0.30), "方法沉淀\n模板、流程、知识库"),
        ((0.13, 0.58), "下一轮拷问\nH/K/R/节奏是否齐备"),
    ]
    for xy, label in nodes:
        box(ax, xy, 0.20, 0.11, label, fc=COLORS["panel"], ec=COLORS["line"], size=11)

    centers = [(x + 0.10, y + 0.055) for (x, y), _ in nodes]
    for a, b in zip(centers, centers[1:] + centers[:1]):
        arrow(ax, a, b, color=COLORS["cyan"], lw=2.0, rad=0.08)

    box(ax, (0.385, 0.405), 0.23, 0.12, "更高的\n内容确定性", fc="#ecfeff", ec=COLORS["cyan"], lw=2.0, size=14)
    save(fig, "fig_creator_flywheel")


def team_chain():
    fig, ax = setup("team_chain", size=(12, 7))
    text(ax, 0.5, 0.93, "团队工业化协作链路", size=22, weight="bold")
    text(ax, 0.5, 0.875, "创意与执行分离，让编导专注内容，让制片控制项目、成本和周期", size=12, color=COLORS["muted"])

    xs = [0.06, 0.25, 0.44, 0.63, 0.82]
    labels = [
        "选题 / 目标\n频道定位与受众",
        "编导\n创意、脚本、内容判断",
        "制片\n预算、周期、执行风险",
        "专职人才\n摄影、技术、专业顾问",
        "成片 / 复盘\n传播结果与知识沉淀",
    ]
    colors = [COLORS["blue"], COLORS["green"], COLORS["amber"], COLORS["violet"], COLORS["cyan"]]
    for x, label, color in zip(xs, labels, colors):
        box(ax, (x, 0.47), 0.13, 0.18, label, fc="#ffffff", ec=color, lw=2.0, size=10.5)
    for i in range(len(xs) - 1):
        arrow(ax, (xs[i] + 0.13, 0.56), (xs[i + 1], 0.56), color=COLORS["line"], lw=2.0)

    box(ax, (0.25, 0.22), 0.70, 0.12, "工业化的收益：产能提高、流程可控、专业能力聚合；仍需保留创作者经验对关键创意的把关。", fc="#f1f5f9", ec=COLORS["line"], size=12)
    arrow(ax, (0.50, 0.47), (0.55, 0.34), color=COLORS["red"], lw=1.8, rad=-0.2)
    text(ax, 0.16, 0.31, "风险点：纯流程无法复制全部个人经验", size=11, color=COLORS["red"], ha="left")
    save(fig, "fig_team_industrial_chain")


def okr_matrix():
    fig, ax = setup("okr_matrix", size=(12, 7.5))
    text(ax, 0.5, 0.94, "OKR 三层目标矩阵", size=22, weight="bold")
    text(ax, 0.5, 0.89, "方向由公司把住；路径由团队拆解；指标要能逼近真实结果", size=12, color=COLORS["muted"])

    headers = ["O：目标", "KR：可观察结果", "为什么能约束行为"]
    col_x = [0.06, 0.31, 0.68]
    col_w = [0.22, 0.34, 0.26]
    for x, w, h in zip(col_x, col_w, headers):
        box(ax, (x, 0.78), w, 0.08, h, fc="#e0f2fe", ec=COLORS["blue"], lw=1.6, size=12)

    rows = [
        ("扩增影响力", "3 个月增粉约 50 万\n全站榜单 Top20 内容 3 条\n至少 1 条榜一", "把播放量刷量风险\n转为平台竞争指标"),
        ("商业营收", "周期毛利目标\n自有服装销售 4 万件以上\n广告与电商同时看", "让内容质量服务\n可持续经营"),
        ("流程迭代 / 知识沉淀", "知识库内部评分 80+\n季度深度反馈达成率 90%+", "把组织能力变成\n可复用资产"),
    ]
    y = 0.60
    for idx, row in enumerate(rows):
        row_color = ["#eff6ff", "#f0fdf4", "#fff7ed"][idx]
        for x, w, val in zip(col_x, col_w, row):
            box(ax, (x, y), w, 0.13, val, fc=row_color, ec=COLORS["line"], lw=1.2, size=10.5)
        y -= 0.17

    text(ax, 0.5, 0.09, "注意：OKR 的价值在于用少数结果指标逼团队自己选择路径。", size=12, color=COLORS["muted"])
    save(fig, "fig_okr_three_layer_matrix")


def commercialization_paths():
    fig, ax = setup("commercialization_paths", size=(12, 7))
    text(ax, 0.5, 0.93, "商业化路径：内容公司怎样形成现金流", size=22, weight="bold")
    text(ax, 0.5, 0.875, "访谈里把收入说成三块，同时强调创意权决定广告服务的议价位置", size=12, color=COLORS["muted"])

    box(ax, (0.40, 0.71), 0.20, 0.10, "粉丝经济 / 影响力", fc="#eef2ff", ec=COLORS["violet"], lw=2.0, size=14)
    revenue = [
        ((0.08, 0.48), "广告\n品牌投放 / 商单", COLORS["blue"]),
        ((0.40, 0.48), "商业服务\n全案、直播业务", COLORS["green"]),
        ((0.72, 0.48), "电商\n自有产品", COLORS["amber"]),
    ]
    for xy, label, color in revenue:
        box(ax, xy, 0.20, 0.13, label, fc="#ffffff", ec=color, lw=2.0, size=13)
        arrow(ax, (0.50, 0.71), (xy[0] + 0.10, xy[1] + 0.13), color=color, lw=1.8)

    box(ax, (0.08, 0.22), 0.36, 0.13, "广告服务的关键：掌握创意权\n否则容易沦为可替代制作环节", fc="#eff6ff", ec=COLORS["blue"], lw=1.6, size=12)
    box(ax, (0.56, 0.22), 0.36, 0.13, "自有产品的关键：用内容建立认知\n再用产品承接长期价值", fc="#fff7ed", ec=COLORS["amber"], lw=1.6, size=12)
    arrow(ax, (0.18, 0.48), (0.26, 0.35), color=COLORS["blue"], lw=1.8)
    arrow(ax, (0.82, 0.48), (0.74, 0.35), color=COLORS["amber"], lw=1.8)
    text(ax, 0.5, 0.10, "补充风险：平台与品牌预算收缩时，中腰部投放会先承压，头部影响力更安全。", size=12, color=COLORS["red"])
    save(fig, "fig_commercialization_paths")


def mission_iteration_relation():
    fig, ax = setup("mission_iteration_relation", size=(12, 7))
    text(ax, 0.5, 0.93, "使命 / 迭代 / 作品关系图", size=22, weight="bold")
    text(ax, 0.5, 0.875, "使命给方向，迭代给抗压能力，作品承担最终的情绪和价值输出", size=12, color=COLORS["muted"])

    box(ax, (0.08, 0.58), 0.22, 0.13, "使命\n让中国人燃起来", fc="#fff1f2", ec=COLORS["red"], lw=2.0, size=13)
    box(ax, (0.39, 0.58), 0.22, 0.13, "方法\n无限进步 / 持续迭代", fc="#ecfeff", ec=COLORS["cyan"], lw=2.0, size=13)
    box(ax, (0.70, 0.58), 0.22, 0.13, "能力底座\n技术 + 商业模式", fc="#f0fdf4", ec=COLORS["green"], lw=2.0, size=13)
    box(ax, (0.39, 0.29), 0.22, 0.13, "输出\n打动人心的作品", fc="#eef2ff", ec=COLORS["violet"], lw=2.0, size=13)
    box(ax, (0.08, 0.29), 0.22, 0.13, "观众变化\n多一点期待 / 眼里有光", fc="#fffbeb", ec=COLORS["amber"], lw=2.0, size=12)
    box(ax, (0.70, 0.29), 0.22, 0.13, "未来目标\n全球表达 / 电影奖项", fc="#ffffff", ec=COLORS["line"], lw=1.6, size=12)

    arrow(ax, (0.30, 0.645), (0.39, 0.645), color=COLORS["line"])
    arrow(ax, (0.61, 0.645), (0.70, 0.645), color=COLORS["line"])
    arrow(ax, (0.81, 0.58), (0.56, 0.42), color=COLORS["line"], rad=-0.15)
    arrow(ax, (0.39, 0.35), (0.30, 0.35), color=COLORS["line"])
    arrow(ax, (0.19, 0.42), (0.19, 0.58), color=COLORS["line"])
    arrow(ax, (0.61, 0.35), (0.70, 0.35), color=COLORS["line"])
    text(ax, 0.5, 0.12, "这张图适合放在总结章：内容形态会变，核心仍是让作品触达人心。", size=12, color=COLORS["muted"])
    save(fig, "fig_mission_iteration_work_relation")


if __name__ == "__main__":
    burst_four_traits()
    creator_flywheel()
    team_chain()
    okr_matrix()
    commercialization_paths()
    mission_iteration_relation()
