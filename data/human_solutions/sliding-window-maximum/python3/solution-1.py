from collections import deque

def sliding_window_maximum(nums, k):
    answer = []
    window = deque()
    
    for i, num in enumerate(nums):
        while window and nums[window[-1]] < num:
            window.pop()
        
        window.append(i)
        
        if window[0] == i - k:
            window.popleft()
        
        if i >= k - 1:
            answer.append(nums[window[0]])
    
    return answer