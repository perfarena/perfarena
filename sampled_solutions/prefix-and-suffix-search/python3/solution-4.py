def minCostClimbingStairs(cost):
    step1 = step2 = 0
    for i in range(len(cost) - 1, -1, -1):
        currentStep = cost[i] + min(step1, step2)
        step1, step2 = step2, currentStep
    return min(step1, step2)

# Example usage
cost = [10, 15, 12, 10]
print(minCostClimbingStairs(cost))  # Output: 15