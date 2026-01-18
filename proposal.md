## 草案

KeepBalances, Arbitrage 这个几个老的也可以删了，因为arbitrage需要重写，这是一个与StaticPositions对应的策略之一。名字叫MarketNeutralPositions，即全名即对冲市场中性策略。
目前它支持
i）现货-现货/合约套利，即跨平台 低价购买 -> 链上转账 -> 高价售卖，同时购买等值的空合约。
ii）现货/合约套利，即著名的资费率套利
iii）合约/合约套利

这里阐明一个新的特性，scope，整个数据驱动的架构其实依赖于simpleeval实现的safe_eval表达式，因此有必要实现一个全局的VirtualMachine和scope系统，在求值时使用ChainMap的特性实现注入local变量的功能，变量根据不同的地方被注入到不同的层级。一个典型的层级可能是 global scope -> exchange class level scope -> exchange instance level scope -> trading pair instance level scope，或者 global scope -> exchange class level scope -> trading pair class level scope -> trading pair instance level scope，策略的加载可能经有不同的路径，但这个箭头逆转层级就会出现矛盾。有scope，那么executor的执行流程就相当清晰了。(app conf中能动态添加多个scopes，id相同的scope也可能有多个不同实例) scopes在app中定义，但其它app中可以使用它

```yaml  app conf
scopes:  # 这里写的key其实是scope class id，另外scope还有scope instance id，同一个类可能有多个scope class id，同一个scope class id的也可能有多个scope instance id，注意，因为可以多次引用scope的parents是一个数组parent的scope class id并非唯一，只是这里并不需要计算，因为完全可以采用惰性生成的方案。
  global:
    class: GlobalScope
    children: ["exchange_class"]
  exchange_class:
    class: ExchangeClassScope
    children: ["exchange", "trading_pair_class", "trading_pair_class_group"]
  exchange:
    class: ExchangeScope
    children: ["trading_pair"]
  trading_pair: ...
  trading_pair_class:
    class: TradingPairClassScope
    children: ["trading_pair"]
  trading_pair_class_group:
    class: TradingPairClassGroupScope
    children: ["trading_pair_class"]
```

现在开始计算，首先需要配置links：

```yaml  strategy conf
# 有一些新的字段，定义了包含或者排除
include_symbols: ['...']  # , (默认为 ['*'])
exclude_symbols: ['...']
exchanges: ['*'] # （ '*'表示包含在app中定义的所有exchanges，默认为 ['*']）

requires: ['medal_amount', 'ticker']  #我还需要实现一个MedalAmountDataSource (它的实现是获取所有的合约/现货账户里的真实存量, 然后形成一个标准的单位字段变量amount, 用于获取该exchange path的存货量，因为是exchange instance level的, 因此indicator自动注入它的amount变量到了scope class为ExchangeScope的类中)
这里使用 requires: ['medal_amount'] 将其加载到此MedalAmountDataSource的exchange instance level scope中。
links: 
  - ["global", "exchange_class", "exchange", "trading_pair_class_group", "trading_pair_class", "trading_pair"]
  - ...
scopes:
  global:
    vars:  # 将在每个scope class id匹配的地方进行计算
      max_trading_pair_groups: 10
      max_position_usd: 2000
      weights:  # 这个变量（常量为dict）
        okx/a: 0.1
        okx/b: 0.1
  trading_pair_class_group:
    vars:
      group_min_price: min([scope['mid_price'] for scope in children.values()])
      # mid_price由ticker datasource注入，这里children是scope机制的特性，这里就能访问trading pair class level scope中注入的mid_price变量，scope是一个重大的特性
    # vars 支持条件变量（通过 on 字段）
  trading_pair:
    vars:
      weight: weights[symbol]
      ratio_est: weight * (group_min_price * amount) / max_position_usd
      # 这里ratio_est和weight是特殊变量,amount是MedalAmountDataSource注入的变量，ratio_est的作用是计算出特殊变量ratio
target_scope: trading_pair
target:
  vars: ...
  condition: ...
```

计算过程是：
对于每个links都执行相同的过程：惰性初始化 (scope_class_id, scope_instance_id): ScopeInstance，并永久缓存Scope。scope instance的创建是由Strategy决定的,它也具有默认行为(它会根据parent_scope_instance、scope_class、global_scope_cache)决定如何返回，例如 ExchangeClassScope 可以根据全局的exchange class，和load marks返回的所有可交易trading pairs，创建scope_instance_id命名为symbol的trading_pair_class。因此为了支持更多类的scope，策略可以集成BaseScope并自定义创建的方法，其中TradingPairClassGroupScope就是这个策略自己实现的，不在标准实现范围中。在所有level的scope都初始化之后，执行indicator注入，然后再次沿着link从前往后，依次计算所有children的vars -> 计算完了再计算conditional vars这和之前的说法一样。最后进入专有计算流程。（例如，计算targets并传递结果到executor中，其实这个传递很简单，就是传递到了最底层trading pair instance level scope中，executor也可以使用相同的scope，且target也具有特殊的condition字段，决定executor是否执行该pair的算法）
因此，executor获取的target的计算结果最终被展开到trading_pair_class中，而executor再次将被触发的trading_pair_class激活，然后循环它的children到trading_pair的scope执行真实订单。因此有几个新增字段

```yaml
order_scope: trading_pair_class
order_levels: ...
order: ...
orders: ...
entry_order_scope: trading_pair_class
exit_order_scope: trading_pair_class
# ...
```

为了显示访问每个scope种的变量，有两个特殊的变量，上游和下游间通过parent(一般很少用到，因为通过ChainMap本身就能访问父域的，这里因为是instance引用，所以parent变为了单一的值)和children(一个{id: child_scope}的字典)

FAQ：

- executor的执行scope是哪个？当然是trading pair instance level scope，因为要对每个真实账户可交易的每个scope里的var执行订单操作和管理。
- indicator的scope是哪个？当然是由indicator的特性决定的，例如 指标类，通常就处于trading pair class level scope，然而例如EquationDataSource，层级就处于exchange instance level scope。
- 这个strategy定义的一个trading pair class group level scope处于哪个level？它显然处于trading pair class level scope的上游，exchange class level scope。


## 以下运行在trading pair class level

```yaml
max_trading_pair_groups: 10 # 最多返回最大交易组的个数
trading_pair_fair_price: fair_price  # 来自FairPriceIndicator注入（这个需要通过注入indicator来实现），这个是一个计算公平价值的衡量指标，fair_price可能为None，表示该交易对无法被处理，这提供了一种机制，用来mask掉那些暂时不满足交易的、不用来统计的币对。对每一个exchage_class, symbol，都计算标准价格，标准价格为1
entry_price_threshold: 0.001
exit_price_threshold: 0.0005 # （规定必须entry_price_threshold>exit_price_threshold>=0）
score_threshold: 0.001
ratio_est: sum([scope["ratio_est"] for scope in children.values()]) 
# 策略的总使命是使ratio总和始终为0, ratio_est的作用是计算出特殊变量ratio
group_condition: #  (这里提供了一种动态能力，可以舍弃某些pairs)
```

专用的计算流程是（是在links计算完vars之后的流程）

1. 对每个币对，运行在trading pair class level上，计算出变量顺序为 ratio_est -> trading_pair_std_price，并排除掉排除掉那些trading_pair_std_price为None的。如果len(group)为0，则该group不在参与后续计算了，也不再传入executor。再次计算ratio_est变量。现在group中还剩下多少trading pair class记为len(group)（你可以自己用办法传递它）。对于 group_condition 计算为false的pair组，同样不再参与计算。
2. 现在转到trading pair class group level上，取出children中trading_pair_std_price最大的和最小的，记为
fair_price_min, fair_price_max，并且score = fair_price_max - fair_price_min
1. 现在再次转入trading pair class level，计算(delta_min_price, delta_max_price) 这两个direction变量，分别计算出(delta_min_direction, delta_max_direction)，其中direction \in {-1,0,1,null}。其中-1/1代表entry的方向（建议开仓），0代表exit（建议平仓），null代表（建议hold）。
2. 对于len(group) == 1的，那么delta_min_direction和delta_max_direction均应设置为0, 计算的rate为0
3. 对于len(group) >=2 的，若记delta_min_price = trading_pair_std_price - fair_price_min， delta_max_price = fair_price_max - trading_pair_std_price。
  现在根据表格中标准计算direction
  | condition |     delta_min_price->delta_min_direction        |  delta_max_price->delta_max_direction |
  | > entry_price_threshold |       -1            |          1            |
  | > exit_price_threshold |        0             |          0            |
  |   else                 |         null         |          null         |

然后是排序,该策略会通过max_trading_pair_groups,只返回两类group中的所有trading pair class当成target返回：
1. 检查任意该策略所包含的exchange中的所有具有仓位的币种（包含合约positions和现货balance），计算其group key，这类group中的优先选择
2. 对于所有group，按照scope排序，从高到低，且score满足score_threshold，直到或group set数量大于等于max_trading_pair_groups或者遍历完所有group。

现在开始在每个trading pair class level计算ratio，对每个trading pair class
对于len(group) <= 1 的，ratio = 0，对于len(group) >= 2的，按照如下规则：

1. 计算第一遍，首先 ratio = clip(ratio_est, -1, 1)
2. 然后根据(delta_min_direction, delta_max_direction)的组合，最多4 x 4=16种来处理不同的ratio 的table，有些情况不存在，需要raise ValueError,（理论上不是代码bug根本走不到那）,计算结果如下
  (-1, -1): raise
  (-1, 0) : min(ratio, 0)
  (-1, 1): ratio
  (-1, null): -1
  (0, -1): raise
  (0, 0): ratio
  (0, 1): max(ratio, 0)
  (0, null): min(ratio, 0)
  (1, -1): raise
  (1, 0): raise
  (1, 1): raise
  (1, null): raise
  (null, -1): raise
  (null, 0): max(ratio, 0)
  (null, 1): 1
  (null, null): ratio

3. 然后在trading class group level，将group内的所有ratio加起来，如果为正，则组中 trading_pair_std_price
  最大的哪个ratio则先减去这个值；否则如果为负数，trading_pair_std_price最小的那个减去这个值（等价于加上其绝对值）。这样ratio的总和就对齐到了0.

4. 然后用最小的那个的ratio减去最大的那个的ratio再除以2，再减1，即
  /delta ratio = (ratio(Price_min) - ratio(Price_max)) / 2 - 1，然后令
  ratio(Price_min) -= /delta ratio, ratio(Price_max) += /delta ratio。因此满足了ratio(Price_min) - ratio(Price_max) = 2，这样group内的所有target的ratio就被确定了，因此ratio这个变量就当成trading class的var，由于executor执行在trading class level或者trading instance level因此executor能够访问。
  
现在根据计算出来的选中的所有group，展开得到target_pairs，最后再和之前一样展开target传回executor。
