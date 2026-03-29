---
title: "Scale-SWE 深度解读：从 6M Pull Requests 到 100K 训练实例，SWE Agent 的数据工程全景"
author: Jason
digest: "面向 RL 从业者的 SWE Agent 数据构建技术拆解：三 Agent 协作流水线、trajectory distillation、以及为什么真实数据碾压合成数据"
---

# Scale-SWE 深度解读：从 6M Pull Requests 到 100K 训练实例，SWE Agent 的数据工程全景

> 论文：*Immersion in the GitHub Universe: Scaling Coding Agents to Mastery*
> 作者：人大高瓴 + 字节跳动 + AweAI Team | arXiv: 2602.09892

如果你做过 RL，一定深知一个道理：**环境（environment）的质量决定了 agent 的上限**。Atari 有 ALE，机器人有 MuJoCo，围棋有 AlphaGo 的自对弈框架。但到了软件工程领域，我们的 "环境" 长什么样？

答案是：一个能跑的 Docker 容器 + 一组能验证对错的单元测试 + 一段描述清楚的 problem statement。这三样东西凑齐，就构成了一个 SWE-bench 风格的 task instance。听起来简单，做起来极难——这正是 Scale-SWE 要解决的核心问题。

## 一、SWE Agent 训练的「环境瓶颈」：为什么数据这么难搞

做 RL 的人都熟悉 reward shaping 的痛苦，但 SWE agent 训练面临的困难比 reward shaping 更底层——**你连一个能跑的环境都搭不起来**。

具体来说，构建一个 SWE 训练实例需要解决三个递进的难题：

**难题一：环境配置（Environment Setup）**。每个 GitHub repo 的构建方式都不一样。有的用 `setup.py`，有的用 `pyproject.toml`，有的依赖特定版本的系统库。一个 repo 的环境配置可能就要花一个经验丰富的开发者半天时间。想象一下要处理 5200 个 repo——这不是人力能解决的问题。

**难题二：单元测试生成（Unit Test Generation）**。SWE-bench 的核心验证机制是 Fail-to-Pass（F2P）测试：这些测试在 buggy 版本上失败，在修复后通过。但大量真实 repo 根本没有足够的单元测试。你需要自动生成能准确捕捉 bug 语义的测试——这本身就是一个需要 agent 交互式探索和自我修正的复杂任务。

**难题三：问题描述合成（Problem Statement Curation）**。GitHub PR 的描述经常是在修复之后写的，会泄露解决方案细节。更多 PR 干脆没有描述。你需要一个 agent 来理解 PR 的意图，然后写出一段既完整又不泄露答案的 problem statement。

现有的方案要么规模太小（SWE-Gym 只有 11 个 repo、2400 个实例），要么靠合成数据走捷径（SWE-smith 用 rule-based 方法生成 50k 实例，但 bug 类型分布严重偏斜）。Scale-SWE 的目标是：**用 agent 自动化整个流水线，从真实 repo 中大规模挖掘高质量训练数据**。

## 二、三 Agent 流水线：SWE 数据构建的 "Multi-Agent MDP"

如果你习惯用 MDP 的视角看问题，Scale-SWE 的流水线可以理解为一个三阶段的 sequential decision process，每个阶段由一个专门的 agent 负责。

### Agent 1：Environment Builder Agent (EBA)

**任务**：把一个裸 Docker 容器变成能运行目标 repo 的完整环境。

形式化地说：`D_final = EBA(R, D_init)`，其中 R 是目标 repo，D_init 是基础 Docker 镜像。

**关键设计**：EBA 不是简单地跑 `pip install`。它需要自主探索 repo 结构，分析 `setup.py`、`pyproject.toml`、`README.md` 等配置文件，推断依赖关系，然后通过交互式执行来解决依赖冲突。这是一个典型的 exploration-exploitation 过程——agent 通过终端反馈来修正自己的安装策略。

**扩展性技巧**：为每个 PR 单独构建环境太贵了。Scale-SWE 的做法是每个 repo 最多为 10 个 PR 做完整环境构建，其余 PR 复用 "最近" 的可用环境（按 PR ID 排序作为时间线的近似）。平均每个 repo 产出 19 个有效实例，实现了极高的环境复用率。

### Agent 2：Unit-test Creator Agent (UCA)

**任务**：`U = UCA(M, R, D_final)`，从 PR 元数据和 repo 上下文生成 F2P 和 P2P 测试套件。

**关键难点**：生成测试不是静态代码分析能搞定的事。Agent 需要理解跨文件交互、数据流、异常处理和边界情况。UCA 在 EBA 构建好的沙盒环境中运行，可以实时执行代码，形成一个 **execute → analyze → refine** 的闭环。

如果你做过 RL 的 reward function design，UCA 的角色本质上就是在为 SWE agent 设计 reward signal——F2P 测试通过就是 +1，不通过就是 0。测试的质量直接决定了训练信号的质量。

### Agent 3：Problem Statement Writer Agent (PSWA)

**任务**：`S = PSWA(M, U, R, D_final)`，基于 PR 元数据和 UCA 生成的测试套件，合成一段自包含的问题描述。

**为什么不直接用 PR 描述？** 因为 PR 描述是 "回顾性" 的——开发者在已经知道解决方案的情况下写的，经常会泄露实现细节。而且 F2P 测试可能会调用原始代码库中不存在的函数或类，problem statement 必须明确描述这些需求，否则任务就不可解。

**模型选择**：PSWA 用的是 Gemini3-Pro 而不是 DeepSeek，因为实验表明 Gemini 生成的问题描述更一致、更严谨，信息泄露更少。这个细节说明不同 LLM 在不同子任务上的适用性差异很大。

## 三、数据质量控制：被忽视的关键细节

### 反作弊机制

评估时一个容易被忽略的漏洞：SWE agent 可以通过 `git log --all` 直接查看 ground truth solution。Scale-SWE 在初始化任务环境后立即执行一个清理脚本，删除所有远程引用、tags、packed-refs、日志，并做 aggressive gc。这是一个非常实际但经常被忽视的 evaluation integrity 问题。

### Rule-based 过滤

只保留满足以下条件的实例：(1) 所有 P2P 测试在 buggy 版本上通过且 F2P 测试失败；(2) 打上 golden patch 后所有测试通过。这是最基本的 consistency check。

### 人工审计

4 位博士生交叉审核了 100 个随机样本，确认 94% 有效。这个 94% 的通过率对应的是 environment + tests + problem statement 三者同时正确。

## 四、从数据到模型：Trajectory Distillation 的实操

这部分是对 RL 从业者最有参考价值的。

**Distillation 流程**：从 100k 实例中选取 25k 子集，用 DeepSeek-V3.2 作为 expert policy，对每个实例做 5 次独立采样（temperature=0.95，最多 100 轮交互）。只保留最终通过所有测试的 trajectory。最终产出 71,498 条高质量轨迹，总计约 35 亿 token。

**这里有几个值得注意的设计选择**：

**为什么用 SFT 而不是 RL？** 论文选择了 distillation + SFT 的路线，而不是直接用 RL（如 SWE-RL 那样）。原因可能是 SWE 任务的 action space 极大（每一步都是自由文本命令），episode 极长（平均几十轮交互），reward 极稀疏（只在最后验证时才知道对错）。在这种设置下，SFT on expert trajectories 是比 online RL 更稳定的起步方式。

**Loss masking**：只对 assistant turns 中产生 well-formed actions 的部分计算 loss。这意味着 agent 的 "思考" 过程不参与梯度更新，只有实际操作（编辑文件、执行命令）才被强化。

**长上下文处理**：训练时 context length 131,072，推理时扩展到 262,144。SWE 任务的输入天然很长（整个 repo 结构 + 代码文件 + 历史交互），长上下文能力是 SWE agent 的刚需。

## 五、实验结果：真实数据 vs 合成数据的对决

最核心的结果在 Table 4 的公平对比实验中：

| 训练数据 | SWE-bench Verified 通过率 |
|----------|------------------------|
| SWE-Gym（真实，11 repo）| 54.8% |
| SWE-smith（合成，128 repo）| 54.6% |
| **Scale-SWE（真实，5.2k repo）** | **64.0%** |

注意 SWE-smith 有 50k 实例，SWE-Gym 只有 2.4k，但性能几乎一样。这说明**合成数据的边际收益递减非常快**——规模翻了 20 倍，性能几乎没涨。而 Scale-SWE 用真实数据实现了显著提升。

从 bug 类型分布来看（Figure 3），合成数据集（如 SWE-smith）严重偏向 Logic Error 这一种类型，而 Scale-SWE 在 API Mismatch、State Sync、Security、I/O Resource 等 10 种 bug 类型上分布均匀。**数据多样性，而非数据规模，才是性能提升的关键。**

## 六、对 RL 从业者的启示

### 1. 环境构建是 SWE RL 的真正瓶颈

在 Atari 或 MuJoCo 中，环境是现成的。但在 SWE 中，构建环境本身就是一个需要 agent 解决的问题。Scale-SWE 用 EBA 自动化了这一步，但这个 agent 本身的训练和可靠性仍是开放问题。

### 2. Reward 设计 = 测试生成

SWE agent 的 reward function 本质上就是单元测试。UCA 生成测试的质量直接等价于 reward signal 的质量。如果你在做 SWE 方向的 RL 研究，测试生成可能比 policy optimization 更值得投入。

### 3. SFT 先行，RL 后续

当前 SWE agent 领域的主流路线是先用 expert distillation + SFT 建立基线，再用 RL 做进一步优化（如 SWE-RL）。Scale-SWE 走的是第一步。这跟 LLM 训练的 pretrain → SFT → RLHF 范式一脉相承。

### 4. 合成数据有天花板

这篇论文最强的 takeaway 之一：SWE-smith 50k 合成实例打不过 Scale-SWE 25k 真实实例。对于想用 LLM 大量生成训练数据的研究者来说，这是一个重要的警示——合成数据的 diversity 不够，会导致 agent 在分布外任务上泛化能力差。

### 5. 64% 意味着什么？

在 SWE-bench Verified 上，Scale-SWE Agent（Qwen3-30B-A3B，只有 3B 激活参数）达到 64%，超过了 SWE-RL（Llama3-70B，41%）和 SWE-Fixer（Qwen2.5-72B，32.8%）。参数效率极高。但距离闭源模型（GPT-5.2 80%，Claude Sonnet 4.5 77.2%）还有 gap。这个 gap 是数据的问题、模型的问题、还是 scaffolding 的问题？值得进一步探索。

## 七、局限和未来方向

当前 Scale-SWE 只支持 Python。论文提到未来会扩展到 Java、C/C++、Rust。对于非 Python 语言，环境配置的复杂度会更高（C++ 的构建系统之混乱有目共睹），EBA 需要更强的泛化能力。

另一个没有深入讨论的方向是**直接在 Scale-SWE-Data 上做 online RL**。71k trajectories + SFT 是一个起点，但如果能在 100k 实例构成的环境中做 PPO/GRPO 式的强化训练，理论上应该能进一步提升。这也正是 SWE-RL 那条线在探索的方向。

---

**总结一句话**：Scale-SWE 用 multi-agent 自动化了 SWE 训练数据的构建流水线，证明了真实数据 + 环境多样性是训练强大 SWE agent 的关键。如果你是 RL 从业者想转 SWE agent 方向，这篇论文是理解「SWE 世界的环境是怎么搭起来的」的最佳入口。
