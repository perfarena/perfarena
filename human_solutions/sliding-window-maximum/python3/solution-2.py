from collections import deque

def max_sliding_window(nums, k):
    if not nums or k == 0:
        return []
    
    q = deque()
    result = []
    
    for i, num in enumerate(nums):
        while q and nums[q[-1]] < num:
            q.pop()
        
        q.append(i)
        
        if q[0] == i - k:
            q.popleft()
        
        if i >= k - 1:
            result.append(nums[q[0]])
    
    return result

# Test the implementation
nums = [1, 3, -1, -3, 5, 3, 6, 7]
k = 3
print(max_sliding_window(nums, k))  # Output: [3, 3, 5, 5, 6, 7]