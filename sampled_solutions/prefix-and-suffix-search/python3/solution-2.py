def minCostClimbingStairs(cost):
    n = len(cost)
    DP = [0] * n
    DP[0] = cost[0]
    DP[1] = cost[1]
    
    for i in range(2, n):
        DP[i] = cost[i] + min(DP[i-1], DP[i-2])
    
    return min(DP[n-1], DP[n-2])

# Example usage
cost = [10, 15, 20]
print(minCostClimbingStairs(cost))