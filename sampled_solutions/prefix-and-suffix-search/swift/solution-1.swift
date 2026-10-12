func minCostClimbingStairs(_ cost: [Int]) -> Int {
    var dp = Array(repeating: 0, count: cost.count + 1)
    for i in 2..<dp.count {
        dp[i] = min(dp[i - 1] + cost[i - 1], dp[i - 2] + cost[i - 2])
    }
    return dp[cost.count]
}

// Example usage
let costArray = [1, 2, 3]
print(minCostClimbingStairs(costArray)) // Output should be 3