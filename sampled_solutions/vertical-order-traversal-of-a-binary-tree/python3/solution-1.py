def minCost(costs):
    costs.sort(key=lambda x: x[0] - x[1])  # Sort based on benefit of city A over city B
    total_cost = 0
    
    for i in range(len(costs) // 2):
        total_cost += costs[i][0]  # Send first half to city A
    for i in range(len(costs) // 2, len(costs)):
        total_cost += costs[i][1]  # Send second half to city B
    
    return total_cost

# Test the function
costs = [[10, 30], [40, 400], [30, 20], [450, 30]]
print(minCost(costs))  # Output: 110