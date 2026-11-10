# Prepare the result array
result = []
for a, b in costs:
    result.append((a - b, a, b))

# Sort the result array in increasing order of cost differences
result.sort(key=lambda x: x[0])

# Calculate the minimum total cost by selecting the optimal assignment
n = len(costs)
m = n // 2
total_cost = sum(a for diff, a, b in result[:m]) + sum(b for diff, a, b in result[m:])

# Output the minimum total cost
print(total_cost)