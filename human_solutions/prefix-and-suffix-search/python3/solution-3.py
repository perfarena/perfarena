def minCostClimbingStairs(cost):
    for step in range(2, len(cost)):
        cost[step] += min(cost[step - 1], cost[step - 2])
    
    return min(cost[-1], cost[-2])

# Example usage
input_cost = [10, 15, 20]
output = minCostClimbingStairs(input_cost)
print(output)  # Output: 15