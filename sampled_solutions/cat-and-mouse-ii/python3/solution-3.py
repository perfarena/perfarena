# Sorting and Calculating Area
for i in range(m):
    sorted_count = sorted(counts[i], reverse=True)
    for j in range(n):
        answer = max(answer, sorted_count[j] * (j + 1))

return answer