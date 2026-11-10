# Sort the costs based on the delta values
costs.sort(key=lambda x: x[0] - x[1])

# Assign the first half to city a and the second half to city b
total_cost = sum(cost[0] for cost in costs[:n]) + sum(cost[1] for cost in costs[n:])

# Check if swapping assignments would increase the total cost
for i in range(n):
    new_total_cost = sum(cost[0] for cost in costs[:i]) + sum(cost[1] for cost in costs[i:])
    total_cost = min(total_cost, new_total_cost)

return total_cost