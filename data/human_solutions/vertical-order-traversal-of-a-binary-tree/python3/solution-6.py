def min_cost_2n_people(costs):
    costs.sort(key=lambda x: x[0] - x[1], reverse=True)
    result = 0
    n = len(costs) // 2
    for i in range(len(costs)):
        result += costs[i][0] if i < n else costs[i][1]
    return result

# Example usage
costs = [(10, 20), (30, 200), (400, 50), (30, 20)]
total_cost = min_cost_2n_people(costs)
print(total_cost)