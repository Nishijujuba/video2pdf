import { Divider, Grid, H1, H2, Stack, Stat, Table, Text, canvasImage } from 'qoder/canvas';

const COVER =
  'D:\\Project\\video2pdf\\newskill-kimi\\workspace\\当智能体学会越界协作_技术复盘_中文重构v2_20260812_155952\\待删除\\rebuilt_pages\\page_01.png';
const FIG_PAGE =
  'D:\\Project\\video2pdf\\newskill-kimi\\workspace\\当智能体学会越界协作_技术复盘_中文重构v2_20260812_155952\\待删除\\rebuilt_pages\\spot_fig4.png';
const TABLE_PAGE =
  'D:\\Project\\video2pdf\\newskill-kimi\\workspace\\当智能体学会越界协作_技术复盘_中文重构v2_20260812_155952\\待删除\\rebuilt_pages\\spot_tab6.png';
const FINAL_PAGE =
  'D:\\Project\\video2pdf\\newskill-kimi\\workspace\\当智能体学会越界协作_技术复盘_中文重构v2_20260812_155952\\待删除\\final_pages\\final_p66.png';

export default function PdfChinesePolishReport() {
  return (
    <Stack gap={20}>
      <H1>PDF 中文极致优化重建 — 完成报告</H1>
      <Text tone="secondary">
        仅以《当智能体学会越界协作_技术复盘_中文k3重构v1.pdf》（69 页）为唯一事实来源，提取文字层与 27
        张内嵌插图重建 LaTeX 工程，蜂群极致润色中文表达后重新编译，产出 66 页 v2 新 PDF，与原 PDF 并存交付。
      </Text>

      <Grid columns={4} gap={16}>
        <Stat value="2219/2219" label="忠实重建语句片段命中" tone="success" />
        <Stat value="~316" label="中文润色实质性改动" />
        <Stat value="688/688" label="事实锚点（数字/日期/时刻/术语）保留" tone="success" />
        <Stat value="66 页" label="终版 PDF 逐页视觉验收通过" tone="success" />
      </Grid>

      <Divider />

      <H2>关键步骤</H2>
      <Table
        headers={['阶段', '工作内容', '结果']}
        rows={[
          ['1 素材提取', 'PyMuPDF 提取 69 页文字层、27 张 PNG（无损）、逐页渲染基准与图-页-caption 映射', '完整提取'],
          ['2 忠实重建', '7 个子代理按章转录为 main.tex + 9 个 section_*.tex；复刻深色封面、页眉、8 张表格、章内脚注区', '编译通过，语句片段 100% 命中'],
          ['3 极致润色', '6 个并行子代理深度润色：拆长句、去翻译腔、被动改主动、衔接显化、指代统一', '约 316 处改动'],
          ['4 红线机器校验', 'redline_check 与忠实版备份比对 caption/脚注/表格/标题/数字日期 UTC/来源标签', 'REDLINE PASS，修复 4 处子代理残留损伤'],
          ['5 编译验收', 'xelatex 两遍编译，全部 66 页逐页渲染视觉检查', '无溢出、图表完整'],
          ['6 交付', '成品复制到 datain 目录与原 PDF 并存，原文件零改动', '已交付'],
        ]}
      />

      <Divider />

      <H2>变更文件与交付物</H2>
      <Table
        headers={['类别', '路径', '说明']}
        rows={[
          ['成品 PDF', 'datain\\当智能体学会越界协作_技术复盘_中文重构v2.pdf', '66 页，16 MB，与 v1 并存'],
          ['LaTeX 源', 'workspace\\…中文重构v2_20260812_155952\\main.tex + section_01–09.tex', '可继续迭代'],
          ['插图', 'figures\\fig_01.png – fig_27.png', '自源 PDF 无损提取'],
          ['忠实版备份', '待删除\\faithful-backup\\', '润色前基线'],
          ['变更报告', '变更报告_20260812.md', '改动分布与红线声明'],
        ]}
      />

      <Divider />

      <H2>验证证据</H2>
      <Table
        headers={['校验项', '方法', '结论']}
        rows={[
          ['内容零丢失', '源 PDF 全部语句片段对 strip-LaTeX 后的 tex 硬归一化子串匹配', '忠实版 2219/2219 命中'],
          ['事实零改动', '688 个日期/数字/UTC 时刻/技术锚点 token 全量比对', '0 缺失'],
          ['红线一致', 'caption×27、脚注区×6、表格×15、标题、来源标签计数逐项 diff', 'REDLINE PASS'],
          ['审计补漏', '完成审计复查发现并还原 3 处润色残留（9.7 小结句式、「明确的阻断终态」、「共同演进」）', '已修复并重编译交付'],
          ['视觉验收', '66 页逐页渲染检查 + 改动页复查', '通过'],
        ]}
        rowTone={[undefined, undefined, undefined, 'warning', undefined]}
      />

      <Divider />

      <H2>成品页面抽样</H2>
      <Grid columns={2} gap={16}>
        <Stack gap={8}>
          <Text size="small" tone="secondary">封面（复刻深色版式，v2 版本标注）</Text>
          {canvasImage(COVER)}
        </Stack>
        <Stack gap={8}>
          <Text size="small" tone="secondary">插图与 caption 归位（图 4 / 图 5）</Text>
          {canvasImage(FIG_PAGE)}
        </Stack>
        <Stack gap={8}>
          <Text size="small" tone="secondary">大表格重建（表 6 结果账本）</Text>
          {canvasImage(TABLE_PAGE)}
        </Stack>
        <Stack gap={8}>
          <Text size="small" tone="secondary">终版末页（9.7 小结 + 脚注 [F1]–[F10]）</Text>
          {canvasImage(FINAL_PAGE)}
        </Stack>
      </Grid>

      <Divider />

      <H2>最终结论</H2>
      <Text>
        Spec 全部要求已实现并有当前证据支撑：成品《当智能体学会越界协作_技术复盘_中文重构v2.pdf》已交付至 datain
        目录；事实、数字、证据状态词汇、来源标签、图表与英文原文经机器比对零改动；原 PDF 保持不变。
      </Text>
      <Text tone="secondary" size="small">
        生成时间：2026-08-12 · 工作区：workspace\当智能体学会越界协作_技术复盘_中文重构v2_20260812_155952
      </Text>
    </Stack>
  );
}
