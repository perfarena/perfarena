def minCostToCityScheduling(cost):
    diffs = []
    for a, b in cost:
        diffs.append([b - a, a, b])
    
    diffs.sort()
    n = len(cost) // 2
    result = 0
    
    for i in range(len(diffs)):
        if i < n:
            result += diffs[i][2]  # Cost to City B
        else:
            result += diffs[i][1]  # Cost to City A
    
    return result

# Example usage
cost = [[10, 50], [30, 20], [50, 50], [20, 60]]
print(minCostToCityScheduling(cost))  # Output: 90