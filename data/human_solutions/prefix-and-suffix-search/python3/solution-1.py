def minCostClimbingStairs(cost):
    for i in range(2, len(cost)):
        cost[i] += min(cost[i - 1], cost[i - 2])
    
    return min(cost[-1], cost[-2])

# Example usage
cost = [10, 15, 20]
print(minCostClimbingStairs(cost))  # Output: 15