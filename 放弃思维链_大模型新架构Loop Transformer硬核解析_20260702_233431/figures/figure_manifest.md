# Figure Manifest

This manifest is the source of truth for final TeX figure references.

| File | Source interval | Teaching role | Suggested caption |
|---|---:|---|---|
| `fig_01_reasoning_iceberg.jpg` | 00:02:29--00:02:39 | Reasoning capability as a layered problem: multi-hop reasoning, test-time compute, CoT, self-consistency and self-refine. | 推理能力冰山：多跳推理、测试时计算和思维链位于同一条“增加推理过程”的技术谱系上。 |
| `fig_02_single_vs_multihop_llm.jpg` | 00:03:04--00:03:31 | Single-step answer versus multi-hop state tracking. | 单步问答更接近记忆匹配；多跳推理要求模型维护中间状态并逐步组合事实。 |
| `fig_03_cot_token_pipeline.jpg` | 00:04:20--00:04:30 | CoT as a token-mediated loop: decode, append, re-embed, update. | 思维链通过文本 token 形成外部迭代：模型写出中间步骤，再把它们读回上下文。 |
| `fig_04_looping_hidden_states.jpg` | 00:04:52--00:05:04 | Hidden-state loop as the core replacement for text-token reasoning traces. | Loop Transformer 的核心直觉：让模型直接在隐藏状态中递归更新表示。 |
| `fig_05_recurrent_transformer_block.jpg` | 00:05:25--00:05:40 | Reusing a Transformer block as a recurrent module. | 共享 Transformer 块反复作用于残差流，使模型在多步内部迭代中打磨表示。 |
| `fig_06_three_stage_grokking.jpg` | 00:06:00--00:06:24 | Behavioral evidence: memorization, in-distribution generalization, systematic generalization. | 三阶段泛化曲线展示了模型从记忆训练样本走向组合式泛化的过程。 |
| `fig_07_dynamical_system_equation.jpg` | 00:07:43--00:07:52 | Recurrent update as a dynamical system. | 将循环层视为动态系统后，隐藏状态更新可写作 \(h_{t+1}=Ah_t+Bx\) 的简化形式。 |
| `fig_08_instability_norm_loss.jpg` | 00:08:20--00:08:30 | Stability via constrain and normalize. | 约束与归一化用于抑制隐藏状态范数失控，避免损失脉冲和递归发散。 |
| `fig_09_fixed_point_cycle.jpg` | 00:09:20--00:09:37 | Mechanistic evidence: fixed point and cyclic trajectories. | PCA 视角下的固定点和周期轨迹说明递归状态并非随机漂移，而是趋向稳定结构。 |
| `fig_10_stages_of_inference.jpg` | 00:10:02--00:10:45 | Early/middle/late recurrent stages with shared weights. | 即使权重共享，早期、中期、后期递归仍可能因输入状态不同而承担不同功能。 |
| `fig_11_token_choice_routing.jpg` | 00:11:42--00:12:00 | Token-choice upfront routing. | 预分配路由在循环前为 token 选择递归深度，效率高但依赖早期难度判断。 |
| `fig_12_expert_choice_routing.jpg` | 00:12:05--00:12:20 | Expert-choice step-by-step routing. | 逐步路由让 token 在每次递归后选择继续或退出，更灵活但训练稳定性更难。 |
| `fig_13_recursive_kv_cache.jpg` | 00:12:59--00:13:20 | KV cache pressure in recurrent models. | 朴素递归模型即使共享参数，也可能为每个递归深度维护 KV 缓存，导致内存流量膨胀。 |
| `fig_14_mor_cache_tradeoff.jpg` | 00:14:15--00:14:25 | Recursion-wise caching versus recursive KV sharing. | MoR 的缓存策略在表示新鲜度和内存效率之间做权衡。 |
| `fig_15_expressiveness_comparison.jpg` | 00:14:48--00:15:17 | Expressiveness of unique layers versus recurrent shared layers. | 独立深层堆栈通常拥有更强表达自由度；循环共享块是一种受约束的紧凑替代。 |
| `fig_16_depth_comparison.jpg` | 00:15:31--00:16:10 | Real depth versus simulated recurrent depth. | 真实深度提供不同变换；递归深度复用同一函数并依赖状态演化诱导差异。 |
| `fig_17_cot_vs_looped_model.jpg` | 00:16:34--00:17:23 | CoT supervision advantage versus implicit looped reasoning. | CoT 的文本轨迹便于监督、筛选和强化；隐藏状态递归缺少天然中间标签。 |
| `fig_18_application_scenarios.jpg` | 00:17:23--00:17:40 | Likely application scenarios. | 循环架构的潜在落点包括合成数据生成、蒸馏和边缘部署。 |

Layout convention: use `\includegraphics[width=0.82\linewidth,height=0.36\textheight,keepaspectratio]{figures/<file>}` for ordinary screenshots. If a section already has dense prose, reduce to `width=0.74\linewidth,height=0.30\textheight`.
