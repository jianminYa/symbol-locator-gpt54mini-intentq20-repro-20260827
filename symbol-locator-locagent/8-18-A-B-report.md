# A/B Report — random100 vanilla vs symloc

- 数据：`SWE-Explore-Bench/results/random100_{A_vanilla,B_symloc}/locagent_top5.jsonl`
- 每边 100 条，按 instance_id 配对 100 对
- 汇总脚本：`SWE-Explore-Bench/scripts/ab_report.py`
- 明细表：`SWE-Explore-Bench/results/random100_ab_report.md`
- 时间：A 14606s（≈4h），B 16363s（≈4.5h）

## 指标对比(均值,配对 n=100)

| 指标 | A vanilla | B symloc | Δ (B-A) | Δ% |
|---|---:|---:|---:|---:|
| precision | 0.6238 | 0.6954 | +0.0716 | +11.47% |
| recall    | 0.4600 | 0.3305 | -0.1295 | -28.15% |
| f1_score  | 0.4436 | 0.3491 | -0.0945 | -21.30% |
| hit_file_rate     | 0.4972 | 0.5925 | +0.0953 | +19.18% |
| noise_file_rate   | 0.2200 | 0.1612 | -0.0588 | -26.74% |
| hit_region_rate   | 0.4593 | 0.5300 | +0.0707 | +15.38% |
| noise_region_rate | 0.1895 | 0.1237 | -0.0658 | -34.74% |
| weighted_core_coverage | 0.3300 | 0.2670 | -0.0629 | -19.06% |
| context_efficiency | 0.8118 | 0.8901 | +0.0783 | +9.64% |
| optional_coverage  | 0.1114 | 0.0965 | -0.0149 | -13.39% |
| ndcg@100 | 0.8210 | 0.9590 | +0.1380 | +16.80% |
| ndcg@300 | 0.8187 | 0.9661 | +0.1474 | +18.01% |
| ndcg@500 | 0.8249 | 0.9645 | +0.1396 | +16.93% |
| recall@100 | 0.1397 | 0.1776 | +0.0379 | +27.16% |
| recall@300 | 0.2325 | 0.2534 | +0.0209 | +8.99% |
| recall@500 | 0.2798 | 0.2757 | -0.0041 | -1.48% |
| first_useful_hit | 0.8860 | 0.9720 | +0.0860 | +9.71% |

## Token 对比(每题均值)

| 字段 | A vanilla | B symloc | Δ (B-A) | Δ% |
|---|---:|---:|---:|---:|
| agent_prompt_tokens     | 512,798 | 293,145 | -219,654 | -42.83% |
| agent_completion_tokens |   4,432 |   4,303 |     -128 |  -2.90% |
| agent_total_tokens      | 517,230 | 297,448 | -219,782 | -42.49% |
| scorer_prompt_tokens    |       0 |  22,314 |  +22,314 |    — |
| scorer_completion_tokens|       0 |   3,635 |   +3,635 |    — |
| scorer_total_tokens     |       0 |  25,949 |  +25,949 |    — |
| scorer_calls            |    0.00 |    6.23 |    +6.23 |    — |
| **total_tokens**        |**517,230**|**323,397**|**-193,833**|**-37.48%** |

## 结论

**B(symloc)相比 A(vanilla)：**
- ✅ Token 大幅下降：agent 端 -42.5%,总 token(含 scorer 的 25.9k 额外开销)仍 -37.5%
- ✅ Precision 提升 +7.16pp,noise_file/region 分别下降 26.7% / 34.7%,context_efficiency +9.64%,first_useful_hit +8.60pp,ndcg@k 全线 +14~18%
- ❌ Recall -12.95pp、F1 -9.45pp、weighted_core_coverage -6.29pp、optional_coverage -1.49pp

**解读**：symbol-locator 让 agent 收敛更快、更少噪声、命中率更集中(hit_* / ndcg 上升,noise_* / total_tokens 下降),但过早/过窄收敛导致召回率下滑——B 的 top-N regions 更精但漏掉了本该在 gold 里的其他部分。

## 后续建议

1. 分析 recall 下滑的 case:B 里哪些 gold region 没进 top5?是被 scorer 排下去了,还是 agent 根本没探索到?
2. 调 scorer 阈值 / top-K 扩到 top10,可能拿回 recall 而 precision 不至于回落太多
3. 加 gold-vs-B 的 miss 分布(按 repo / 按 num_gold_regions),看是不是长尾 gold(gold≥3)崩得更严重
