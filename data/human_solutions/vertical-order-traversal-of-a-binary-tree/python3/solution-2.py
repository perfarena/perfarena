def min_cost_scheduling(cost_matrix):
    total_sum = 0
    n = len(cost_matrix) // 2
    pq = []
    
    for i in range(len(cost_matrix)):
        total_sum += cost_matrix[i][0]
        heapq.heappush(pq, (cost_matrix[i][1] - cost_matrix[i][0], i))
        
        if len(pq) > n:
            total_sum += heapq.heappop(pq)[0]
    
    return total_sum

# Example usage
cost_matrix = [[10, 20], [30, 40], [50, 60], [70, 80]]
result = min_cost_scheduling(cost_matrix)
print(result)