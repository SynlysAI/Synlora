"""能力目录：内置项枚举、管理员策略与用户安装记录（市场机制）。

三层模型（参考 jiuwen 的目录分层 + DSH 的配置分层）：
1. 内置目录（随仓库，只读）——本包的 CatalogService 枚举；
2. 管理员策略（visible/hidden + 是否默认启用）——CatalogPolicyRepo；
3. 用户安装（只写 DB 记录，不复制文件）——UserCapabilityRepo。

运行期由 CapabilityService 汇总三层，算出「某个用户实际可见」的专家/技能/插件。
"""
