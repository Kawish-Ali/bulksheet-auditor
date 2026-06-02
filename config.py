ACOS_TARGET = 0.30
ACOS_CRITICAL = 0.60
ACOS_HIGH = 0.50

WASTED_ST_THRESH = 0.30       # flag if wasted ST spend > 30% of total SP spend
WASTED_KW_THRESH = 0.05       # flag if wasted keyword spend > 5% of total
BROAD_MATCH_THRESH = 0.50     # flag if >50% of keyword spend is Broad match
ZERO_IMP_THRESH = 0.80        # flag if >80% of enabled keywords have zero impressions
UNDERDELIVERY_THRESH = 0.20   # flag campaigns spending <20% of daily budget
MIN_SPEND_FILTER = 1.0        # ignore rows with spend below $1

SINGLE_KW_AG_LIMIT = 100      # flag ad groups with more than this many keywords
MIN_IMPRESSIONS_FOR_CTR = 100 # min impressions before flagging low CTR

CTR_LOW_THRESH = 0.0015       # 0.15%
CVR_LOW_THRESH = 0.05         # 5%

QUICK_WIN_ACOS_MAX = 0.25     # search terms below this ACoS are promotion candidates
QUICK_WIN_MIN_ORDERS = 1
