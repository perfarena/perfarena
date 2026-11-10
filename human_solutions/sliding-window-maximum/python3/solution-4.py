from collections import deque

def sliding_window_max(nums, k):
    answer = []
    decrease_q = deque()
    
    for i, num in enumerate(nums):
        if i >= k and decrease_q[0][1] == i - k:
            decrease_q.popleft()
        
        while decrease_q and decrease_q[-1][0] < num:
            decrease_q.pop()
        
        decrease_q.append((num, i))
        
        if i >= k - 1:
            answer.append(decrease_q[0][0])
    
    return answer