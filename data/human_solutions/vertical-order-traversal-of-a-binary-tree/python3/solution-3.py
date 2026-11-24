# Sort costs by savings
sorted_costs = sorted(range(len(costs)), key=lambda x: costs[x][1] - costs[x][0])

# Assign candidates to cities based on sorted order
total_cost = 0
for i, idx in enumerate(sorted_costs):
    total_cost += costs[idx][1] if i < len(costs) // 2 else costs[idx][0]

return total_cost