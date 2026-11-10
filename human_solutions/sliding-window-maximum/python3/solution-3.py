import heapq

def sliding_window_max(nums, k):
    answer = []
    heap = []
    
    for i, num in enumerate(nums):
        if i >= k:
            if heap[0][1] == i - k:
                heapq.heappop(heap)
        
        heapq.heappush(heap, (-num, i))
        
        if i >= k - 1:
            answer.append(-heap[0][0])
    
    return answer