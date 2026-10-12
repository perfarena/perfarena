def minCostClimbingStairs(cost):
    dp = [0] * len(cost)
    dp[0], dp[1] = cost[0], cost[1]
    
    for i in range(2, len(cost)):
        dp[i] = cost[i] + min(dp[i-1], dp[i-2])
    
    return min(dp[-1], dp[-2])

# Test the function with the given cost array
cost = [10, 15, 20]
result = minCostClimbingStairs(cost)
print(result)