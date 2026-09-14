# -*- coding: utf-8 -*-
"""
生成测试素材：模拟票据 / 合同，用于验证
    图片 OCR、扫描版 PDF（无文本层）OCR、原生 PDF（有文本层）抽取
三条处理分支。

输出目录：samples/
"""
import os
import sys
from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(os.path.dirname(HERE), "samples")   # 输出到项目根的 samples/
os.makedirs(OUT, exist_ok=True)

FONT_CANDIDATES = [
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
    r"C:\Windows\Fonts\simsun.ttc",
]

W, H = 1240, 1754  # A4 比例


def load_font(size, bold=False):
    for p in FONT_CANDIDATES:
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                continue
    return ImageFont.load_default()


def render(title, rows, body, footer, filename):
    img = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(img)

    f_title = load_font(52)
    f_key = load_font(34)
    f_body = load_font(32)
    f_small = load_font(28)

    y = 90
    # 标题居中
    bb = d.textbbox((0, 0), title, font=f_title)
    d.text(((W - (bb[2] - bb[0])) / 2, y), title, font=f_title, fill="black")
    y += 100
    d.line([(80, y), (W - 80, y)], fill="black", width=3)
    y += 50

    # 键值行
    for k, v in rows:
        d.text((110, y), k, font=f_key, fill="black")
        d.text((420, y), v, font=f_key, fill="black")
        y += 62

    y += 30
    d.line([(80, y), (W - 80, y)], fill="#888888", width=2)
    y += 45

    # 正文（自动换行）
    d.text((110, y), body[0], font=f_body, fill="black")
    y += 58
    max_chars = int((W - 240) / 32)
    for para in body[1:]:
        line = ""
        for ch in para:
            if len(line) >= max_chars:
                d.text((110, y), line, font=f_body, fill="black")
                y += 52
                line = ""
            line += ch
        if line:
            d.text((110, y), line, font=f_body, fill="black")
            y += 52
        y += 14

    # 页脚
    d.text((110, H - 150), footer, font=f_small, fill="#333333")

    path = os.path.join(OUT, filename)
    img.save(path, quality=95)
    print("  生成图片:", path)
    return img


def main():
    print("开始生成测试素材 ...")

    invoice = render(
        title="增值税专用发票",
        rows=[
            ("发票号码：", "4403217890"),
            ("开票日期：", "2026年03月18日"),
            ("购买方名称：", "星海电子科技有限公司"),
            ("销售方名称：", "蓝盾检测技术有限公司"),
            ("项目名称：", "检测服务费"),
            ("金额（不含税）：", "12075.47 元"),
            ("税率：", "6%"),
            ("价税合计：", "12800.00 元"),
        ],
        body=[
            "一、检测项目内容",
            "本次检测为工作场所危害因素检测，覆盖生产车间、喷涂车间、仓储区等作业岗位，"
            "检测项目包括粉尘、苯系物、噪声及高温等作业场所危害因素。",
            "二、结算方式",
            "请于收到发票之日起三十日内完成付款，逾期按日加收万分之五违约金。",
        ],
        footer="销售方（章）：蓝盾检测技术有限公司    开票人：李四",
        filename="01_增值税专用发票.png",
    )

    receipt = render(
        title="收款收据",
        rows=[
            ("收据编号：", "SJ-2026-0402"),
            ("收款日期：", "2026年04月02日"),
            ("交款单位：", "汇通财务咨询有限公司"),
            ("收款方式：", "银行转账"),
            ("收款事由：", "代理记账服务费"),
            ("金额（大写）：", "叁仟伍佰元整"),
            ("金额（小写）：", "¥ 3500.00"),
        ],
        body=[
            "收款说明",
            "兹收到上述单位交来代理记账服务费，服务期间为2026年第一至第二季度，"
            "包含账务处理、纳税申报、财务报表编制及相关税务咨询事项。",
            "本收据一式两联，第一联存根，第二联交款人收执。",
        ],
        footer="收款单位（章）：汇通财务咨询有限公司    收款人：王五",
        filename="02_收款收据.png",
    )

    labor = render(
        title="劳动合同书",
        rows=[
            ("合同编号：", "LD-2026-0188"),
            ("甲方（用人单位）：", "蓝盾检测技术有限公司"),
            ("乙方（劳动者）：", "张三"),
            ("身份证号码：", "440101199001011234"),
            ("合同期限：", "2026年01月01日至2028年12月31日"),
            ("工作岗位：", "检测技术员"),
            ("工作地点：", "示例市示例区"),
            ("月工资标准：", "8500.00 元"),
        ],
        body=[
            "第一条 工作内容",
            "乙方同意根据甲方工作需要，担任检测技术员岗位工作，"
            "负责用人单位工作场所作业场所危害因素的现场采样与检测评价工作。",
            "第二条 劳动保护",
            "甲方应为乙方提供符合国家国家标准的劳动条件和必要的劳动防护用品，"
            "并定期组织定期体检，建立员工健康档案。",
            "第三条 合同变更",
            "本合同的变更、解除和终止，依照《中华人民共和国劳动合同法》的相关规定执行。",
        ],
        footer="甲方（盖章）：蓝盾检测技术有限公司    乙方（签字）：张三",
        filename="03_劳动合同书.png",
    )

    purchase = render(
        title="设备采购合同",
        rows=[
            ("合同编号：", "CG-2026-0520"),
            ("签订日期：", "2026年05月20日"),
            ("需方：", "星海电子科技有限公司"),
            ("供方：", "广诚仪器设备有限公司"),
            ("设备名称：", "气相色谱仪"),
            ("规格型号：", "GC-9800"),
            ("数量：", "2 台"),
            ("合同总金额：", "¥ 156000.00"),
        ],
        body=[
            "第一条 质量标准",
            "供方提供的设备必须是全新未经使用的原厂正品，符合国家相关质量标准，"
            "并随机提供产品合格证、使用说明书及计量检定证书。",
            "第二条 交货与验收",
            "供方应于合同生效后三十日内将设备送达需方指定地点，需方应在收货后十五日内完成验收，"
            "逾期未提出书面异议视为验收合格。",
            "第三条 售后服务",
            "设备整机保修期为十二个月，保修期内因产品质量问题造成的故障由供方免费维修或更换。",
        ],
        footer="需方（盖章）：星海电子科技有限公司    供方（盖章）：广诚仪器设备有限公司",
        filename="04_设备采购合同.png",
    )

    # ---- 扫描版 PDF（纯图片，无文本层）----
    scanned_path = os.path.join(OUT, "05_扫描件合集_无文本层.pdf")
    imgs = [invoice.convert("RGB"), receipt.convert("RGB"), labor.convert("RGB")]
    imgs[0].save(scanned_path, "PDF", save_all=True, append_images=imgs[1:], resolution=150)
    print("  生成扫描版 PDF（无文本层）:", scanned_path)

    # ---- 原生 PDF（有文本层）----
    try:
        import fitz
        native_path = os.path.join(OUT, "06_检测报告_有文本层.pdf")
        doc = fitz.open()
        page = doc.new_page(width=595, height=842)  # A4 pt
        lines = [
            (72, 80, 22, "工作场所危害因素检测报告"),
            (72, 140, 12, "报告编号：JC-2026-0731"),
            (72, 170, 12, "委托单位：星海电子科技有限公司"),
            (72, 200, 12, "检测机构：蓝盾检测技术有限公司"),
            (72, 230, 12, "检测日期：2026年06月15日"),
            (72, 260, 12, "检测类型：工作场所危害因素定期检测"),
            (72, 320, 12, "一、检测范围"),
            (72, 350, 11, "本次检测覆盖注塑车间、喷涂车间、装配车间及仓储区域，"),
            (72, 375, 11, "共设置粉尘采样点12个、苯系物采样点8个、噪声测量点15个。"),
            (72, 415, 12, "二、检测结果"),
            (72, 445, 11, "注塑车间粉尘浓度检测结果符合职业接触限值要求；"),
            (72, 470, 11, "喷涂车间苯系物浓度超标，需加强局部通风设施改造。"),
            (72, 510, 12, "三、整改建议"),
            (72, 540, 11, "建议在喷涂工位增设局部排风装置，并为作业人员配发"),
            (72, 565, 11, "符合标准的防毒面具，同时缩短单次连续作业时间。"),
            (72, 640, 11, "检测人：赵六    审核人：钱七    签发日期：2026年06月22日"),
        ]
        for x, y, size, text in lines:
            page.insert_text((x, y), text, fontname="china-s", fontsize=size)
        doc.save(native_path)
        doc.close()
        print("  生成原生 PDF（有文本层）:", native_path)
    except Exception as e:
        print("  原生 PDF 生成失败（PyMuPDF 未就绪？）:", e)

    print("素材生成完成 ->", OUT)


if __name__ == "__main__":
    main()
