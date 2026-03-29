---
title: "Agent 会自己进化吗？一文讲透通往 ASI 的 Self-Evolving Agents"
author: Jason
template: standard
source_url: https://arxiv.org/abs/2507.21046
---

# Agent 会自己进化吗？一文讲透通往 ASI 的 Self-Evolving Agents

> 论文：A Survey of Self-Evolving Agents: What, When, How, and Where to Evolve on the Path to Artificial Super Intelligence
>
> 作者：Huan-ang Gao 等 27 位作者
>
> 发表：TMLR 2026
>
> 链接：https://arxiv.org/abs/2507.21046

## 一句话总结

这篇综述想回答一个大问题：如果大模型不再只是“会答题”，而是能在任务过程中持续学习、改策略、长记忆、换工具、重构工作流，它离真正的自进化 Agent 还有多远？

![Self-Evolving Agents 路线图](https://arxiv.org/html/2507.21046v4/x1.png)

> 图 1：论文给出的总体路线：从 LLM，到 foundation agents，再到 self-evolving agents，最后通向更强的 ASI 形态。

## 问题与动机

这篇论文的出发点很直接：**今天的大模型很强，但本质上还是“静态的”**。

什么意思？就是模型虽然参数巨大、知识广、推理也不错，但它在部署之后，通常不会真的随着环境变化而持续成长。你今天把它扔进一个新软件仓库、一个新业务流程、一个新工具链，甚至一个每天都在变化的网页环境里，它更多是在“拿已有能力硬解”，而不是像人一样在任务中积累经验、修正策略、升级技能。

这就是今天很多 Agent 系统的核心矛盾：

1. **任务环境是动态的，模型能力却是静态的**。现实世界不是 benchmark。API 会变，网页会改，代码库会演化，用户偏好也会漂移。
2. **单次推理不够，必须长期适应**。很多复杂任务不是“一问一答”就结束，而是需要反复试错、复盘、总结、迁移经验。
3. **靠 prompt patch 不可持续**。今天失败了就补一句提示词，明天再加一个规则，后天又接一个外部 tool。这样堆出来的系统往往越来越脆。

所以作者提出一个更大的研究范式：**Self-Evolving Agents（自进化智能体）**。

这里的重点不是“Agent 会不会调用工具”，而是：

- 它能不能**从交互中学会更好的做法**？
- 它能不能**把经验沉淀成记忆、策略、工具、甚至新架构**？
- 它能不能**在测试时、跨任务、跨会话持续升级**？

这篇文章最有价值的地方，不是又发明了一个新算法，而是把整个领域拆成了四个特别关键的问题：

- **What to evolve**：到底什么东西该进化？是模型参数、memory、prompt、tool，还是 agent workflow？
- **When to evolve**：什么时候进化？是一次任务内部边做边改，还是任务结束后统一复盘？
- **How to evolve**：靠什么信号进化？reward、textual feedback、demonstration、population-based search，还是多 agent 共演化？
- **Where to evolve**：在哪些场景里进化最有价值？通用任务、代码、教育、医疗，还是长期开放环境？

说白了，这篇 survey 在做的事，是把“Agent 如何从一个会干活的系统，变成一个会成长的系统”这件事，第一次系统性讲清楚。

## 方法详解

这篇论文不是 proposing 一个单点方法，而是在构建一套**理解自进化 Agent 的统一坐标系**。你可以把它理解成一张四维地图。

### 一、What to evolve：Agent 到底哪里可以进化？

作者先把“进化对象”拆成四大类：**Model、Context、Tools、Architecture**。

### 1. Model：让模型本身升级

这是最直观的一类。也就是 Agent 在任务过程中，不只是用固定模型推理，而是直接或间接让模型能力变强。

这里包括几种常见路线：

- 通过 self-training / self-improvement，让模型根据自己的成功轨迹继续学
- 用 reward 或 verifier 来优化策略
- 让模型从错误案例里反思，形成更强的 reasoning pattern

这类方法最像我们熟悉的 post-training，只不过目标不再是离线统一训练，而是面向 Agent 在真实环境中的持续演化。

但问题也很明显：更新模型参数很贵、风险高、而且容易破坏已有能力。所以很多系统并不会一上来就改 weights，而是先改 context 层。

### 2. Context：让上下文系统进化

这是我觉得最现实、也最容易率先落地的一类。作者把它拆成两块：**memory evolution** 和 **prompt optimization**。

#### Memory evolution

Agent 每完成一次任务，都可能产生经验：

- 哪种网页结构最容易抽取失败
- 哪种工具参数配置更稳
- 哪种代码库要先跑测试再改
- 哪类用户偏好需要长期记住

如果这些经验只是留在一次对话里，那就浪费了。Self-evolving Agent 的关键能力之一，就是把这些经验提炼成长期可复用的 memory。

所以 memory 不是简单的聊天记录堆积，而是一个不断被写入、筛选、压缩、检索、淘汰的动态系统。

#### Prompt optimization

另一条路线是让 prompt 自己进化。

比如一开始 agent 的 system prompt 或 task prompt 很粗糙，但经过多轮试错之后，它发现：

- 先拆计划再执行更稳
- 遇到未知环境先探测再行动更好
- 某种风格的 tool call 更有效

这些都可以被自动吸收进 prompt、instruction 或 workflow policy 里。这个方向的代表工作很多，比如 APE、PromptBreeder、DSPy、ProTeGi 这类自动优化 prompt / program 的范式。

**直觉上，context evolution 很像给 Agent 长“经验层”和“习惯层”**。人不一定每次都改大脑参数，但会记笔记、改 checklist、调整工作流程。Agent 也是一样。

### 3. Tools：让工具系统进化

作者把 tool evolution 分成三件事：

- **Creation**：能不能自己发明或封装新工具
- **Mastery**：能不能越来越会用现有工具
- **Selection**：能不能越来越聪明地选工具

这很关键。很多人以为 Agent 能调用 tools 就已经很强了，但现实里更难的是：

- 什么时候该用哪个 tool？
- 用失败后如何调整参数？
- 一个任务是否需要组合多个工具？
- 有没有必要把一串操作封装成 reusable skill？

比如一个 coding agent，第一次修 bug 时可能会乱试 grep、test、edit；但如果它经历足够多案例，就应该逐渐学会一套稳定套路：先定位 failing test，再缩小影响范围，再做最小改动，再回归验证。这个过程，本质上就是 tool use 的进化。

### 4. Architecture：让 Agent 工作流和组织形态进化

最激进的进化不是改某个 prompt，而是改**系统结构本身**。

例如：

- 单 agent 变成 planner + executor + critic 的多角色结构
- 原本串行 workflow 改成可回退的 tree search
- 多 agent 协作网络自己搜索最优拓扑
- 某些模块被替换成更适合当前任务的 specialized sub-agent

这类方法往往更 powerful，但也更复杂，因为你不再只是让 Agent “做得更好”，而是在让它“组织自己做事的方式”发生变化。

![自进化 Agent 分类树](https://arxiv.org/html/2507.21046v4/x2.png)

> 图 2：这篇综述最核心的价值之一，就是把自进化 Agent 的研究对象系统拆成 what / when / how / where 四个维度。

### 二、When to evolve：什么时候进化？

作者把时机分成两大类：**Intra-test-time** 和 **Inter-test-time**。

### 1. Intra-test-time self-evolution

就是**任务进行中边做边进化**。

这类方法很像人类现场纠偏：

- 做到一半发现策略不对，立刻重规划
- 看到工具报错，立刻总结失败原因
- 在当前样本里进行 self-reflection
- 用 test-time learning 在当前环境里快速适应

这类方法的优点是反应快，特别适合长任务、交互式任务、开放环境任务。

缺点是：

- 容易局部过拟合当前任务
- 计算预算可能爆炸
- 如果反思机制不可靠，可能越改越乱

### 2. Inter-test-time self-evolution

就是**任务结束后统一复盘，再把经验迁移到后续任务**。

这更像人类的“复盘机制”：

- 这次哪里做错了？
- 哪种策略以后优先用？
- 哪类输入应该触发哪种 workflow？
- 哪些经验可以写成长期 memory / lesson / policy？

它的优势是更稳定，适合做长期累积；缺点是反馈闭环更慢，不能立即拯救当前任务。

我自己的判断是：**真正强的 self-evolving system，最后一定是这两类结合**。

- Intra-test-time 负责现场适应
- Inter-test-time 负责长期沉淀

一个负责“活下来”，一个负责“变更强”。

### 三、How to evolve：靠什么机制进化？

论文把演化机制整理成几个主流路线。

### 1. Reward-based self-evolution

最经典的一类。Agent 做完事以后，根据 reward 或 verifier 信号决定哪些策略该强化。

reward 可以来自：

- 外部环境结果，比如任务是否完成、代码测试是否通过
- 内部打分，比如 self-certainty、self-consistency
- 文本反馈转 reward，比如 critic 给出的语言评价再转优化信号

这个方向的优点是目标明确；缺点是 reward design 很难，稀疏奖励尤其难搞。现实世界任务往往没有标准答案，也没有稳定标量分数。

### 2. Imitation & Demonstration Learning

另一类方法不是靠 reward，而是靠示范。

示范可以来自：

- 模型自己生成的高质量轨迹
- 其他 agent 的成功案例
- 人类专家的 demonstration
- 多来源混合示范

这很像“看高手怎么做，再学”。在很多复杂任务里，示范比 reward 更 dense，也更容易稳定训练。

### 3. Population-based / Evolutionary Methods

这类方法就更像真正的“进化论”了：

- 同时维护多个 candidate policy / prompt / workflow
- 做 variation（变异）
- 做 selection（筛选）
- 让表现更好的个体留下来

像 PromptBreeder、AlphaEvolve、某些自动搜索 workflow 的方法，都有这种味道。

它的魅力在于：不必强行定义梯度，只要能比较优劣，就能演化。

但代价也不小：算力消耗高、搜索空间巨大，而且 evaluation noise 很容易把选择过程搞偏。

### 4. Single-agent vs Multi-agent Co-evolution

作者还特别强调了一个常被忽视的问题：**进化不一定发生在单体 agent 内部，也可以发生在 agent 群体之间**。

单 agent 进化更容易控制，但多 agent 共演化更接近真实复杂系统。

比如：

- planner 和 executor 互相逼着对方变强
- proposer 和 verifier 形成博弈
- 多个 specialized agent 在协作中重组职责
- agent 生态系统随着任务环境一起演化

这一步往前走，其实已经不是“做一个会用工具的大模型”了，而是在做“一个会自组织、自优化的智能生态”。

### 四、Where to evolve：在哪些领域最值得做？

作者最后讨论了应用场景，重点提了几个方向：

- **Coding / SWE**：最适合，因为反馈最清晰，测试可验证，任务链条长，经验复用价值高
- **Education**：可以根据学习者状态不断调整教学策略
- **Healthcare**：适应病人状态、流程变化、知识更新，但安全门槛极高
- **General open environments**：网页、桌面、机器人、真实业务系统

这里一个重要判断是：**不是所有场景都适合“高强度自进化”**。

代码任务之所以是天然试验田，是因为它有明确 reward（测试是否通过）、明确环境状态、明确可复现日志。医疗和金融虽然也需要适应，但安全要求高得多，所以更可能先用“受控演化”，而不是完全开放自我修改。

## 实验分析

这篇是 survey，不是单一算法 paper，所以它没有那种“在 3 个 benchmark 上超 SOTA 15%”的实验结构。它的“实验价值”主要体现在**系统梳理 benchmark、evaluation 目标和研究现状**。

### 1. 这篇综述真正评估的不是一个方法，而是整个赛道

作者做了 77 页的大综述，试图回答：如果我们真的想研究 self-evolving agents，到底该怎么评？

这比想象中难很多，因为传统 LLM benchmark 评的是静态能力，而 self-evolving agent 评的是**适应能力**。

静态评测只问：“你现在会不会？”

自进化评测问的是：“你能不能边做边学，下一次做得更好？”

两者完全不是一回事。

### 2. 论文提出了三层评估范式

作者把评估大致分成三类：

#### 第一类：Static Assessment

就是传统那种一次性评测。

这种评法适合测 base capability，但不适合衡量 evolution。因为一个 agent 即便静态分数一般，只要学得快、复盘强、适应快，它在长期环境里也可能更强。

#### 第二类：Short-Horizon Adaptive Assessment

也就是短期适应能力评测。

看 agent 在少量交互、少量反馈后，性能能不能迅速提升。这个特别适合检验：

- reflexion 有没有用
- test-time learning 有没有用
- prompt / memory 更新能不能立刻改进行为

#### 第三类：Long-Horizon Lifelong Learning Assessment

这是最难、但也是最接近真实世界的一类。

核心问题是：经过很多轮任务后，agent 是否真的在成长，而不是只是在当前 session 里“装聪明”。

这就需要看：

- 长期记忆是否有效
- 旧经验能否迁移到新任务
- 是否出现 catastrophic forgetting
- 进化后是否保持稳定和安全

### 3. 为什么代码任务会成为自进化 Agent 的主战场

作者在应用与评测部分反复强调，SWE / coding 是目前最成熟的试验场。我完全同意。

原因很简单：

1. **反馈清晰**：test pass / fail 就是 reward
2. **环境可重放**：代码仓库、issue、日志都能复现
3. **任务链条长**：适合观察 planning、memory、tool use、reflection
4. **经验可迁移**：很多策略能跨仓库复用

所以你会发现，self-evolving agent 这条路，很多最有价值的 early signal 都会先出现在 coding agent 上，而不是聊天机器人上。

### 4. 论文的一个隐藏贡献：给出了“研究缺口地图”

虽然这篇不是实验论文，但它特别值钱的一点是，它把整个赛道里“哪些地方热、哪些地方空、哪些地方危险”都标出来了。

比如：

- **what to evolve** 里，memory / prompt 研究较多，真正改 architecture 的还相对少
- **when to evolve** 里，短期 adaptation 很热，但长期持续进化的严格评测还不成熟
- **how to evolve** 里，textual feedback 和 reward 很常见，但多 agent 共演化和安全约束还远没打透
- **evaluation** 里，真正能测长期自进化的 benchmark 很稀缺

也就是说，这篇文章虽然不报具体 SOTA，但它提供了一种更稀缺的东西：**让研究者知道下一步该往哪打**。

### 5. 几个我认为最重要的结论

结合全文，我觉得可以提炼出 5 个很硬的判断：

1. **Self-evolution 不是单一技术，而是系统能力**。它不是一个 loss function，而是一整套闭环：反馈、记忆、选择、更新、评估。
2. **Context-level evolution 可能比 parameter-level evolution 更先落地**。因为便宜、稳、可控。
3. **代码环境会继续是最重要的训练场**。因为可验证、可复现、可积累。
4. **长期评测是最大短板之一**。很多工作声称 agent 会“学习”，但其实只是短期 patch。
5. **安全问题会越来越核心**。一个能自我修改的 agent，如果没有约束，就是把不稳定性系统化。

## 我的评价

### 亮点

这篇论文最大的亮点，不在于提出了某个惊艳算法，而在于它第一次把 **self-evolving agents** 从“很多零散工作”整理成了一个清晰研究范式。

它最强的地方有三个：

第一，**框架感很强**。what / when / how / where 这四个维度非常顺手，之后你看任何 agent paper，都可以直接往这四个框里塞。这个是好综述最难得的价值——它不是重复文献，而是提供理解世界的坐标系。

第二，**抓住了真正的问题**。今天很多 Agent 工作还停留在“怎么把工具调得更顺”，但这篇论文盯的是更本质的命题：Agent 能不能变成一个会持续成长的系统。这个方向判断，我认为是对的，而且会越来越重要。

第三，**把 evaluation 问题抬到了台面上**。没有好的长期评测，就没有真正可信的自进化研究。很多看起来很酷的 self-improvement，最后可能只是 prompt 级 cosmetic surgery。作者把这个问题点透了。

### 局限性

当然，这篇文章也有几个明显局限。

1. **它是综述，不是统一实验标准**。它能整理赛道，但不能替你证明哪条路线一定更优。
2. **“通往 ASI” 这个叙事有点大**。学术上可以理解为 vision，但如果把“自进化 agent = 通往 ASI 的必经之路”说得太满，就容易带一点概念拔高。
3. **很多被纳入的工作异质性很强**。有些方法是在改 prompt，有些是在改 memory，有些是在做 RL，有些是在做 workflow search。把它们都叫 self-evolving，视角上是成立的，但严格性上也会让边界变宽。
4. **安全讨论仍然偏宏观**。作者提到了 safe and controllable agents、多 agent ecosystem 等挑战，但真正落地到“如何审计 agent 的自修改行为”，还缺少更硬的方法学。

### 影响

如果你问我，这篇论文对未来 1-2 年最重要的启发是什么，我的答案很简单：

**Agent 的下一阶段，不是谁会更多工具，而是谁能更稳定地从经验里变强。**

这会直接影响三个方向：

- **SWE Agent**：从一次性解题，走向持续维护代码库、积累 repo memory、形成长期工程策略
- **Personal Agent**：从会聊天，走向真正理解用户、记住偏好、适应节奏、优化协作方式
- **Open-World Agent**：从 demo 级 tool calling，走向在动态真实环境里持续生长

所以这篇 survey 的意义，不是“总结过去”，而是**给未来 Agent research 立了一个更高的靶子**。

如果说 2024 年大家还在证明 Agent 能不能 work，2025-2026 年真正该问的就是：

> 它能不能不靠人天天补 prompt，而是自己越做越强？

而这，才是 self-evolving agents 真正迷人的地方。

> **论文链接**：https://arxiv.org/abs/2507.21046
>
> **关键词**：Self-Evolving Agents, Continual Learning, Agent Memory, Tool Evolution, Agent Architecture, ASI
