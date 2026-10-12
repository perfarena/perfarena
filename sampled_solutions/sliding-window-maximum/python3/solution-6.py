def maxSlidingWindow(nums, k):
    if not nums or len(nums) < k:
        return []
    
    result = []
    deck = deque()
    pointer = 0
    
    for i in range(len(nums)):
        # Remove numbers out of the window
        while deck and deck[0] < i - k + 1:
            deck.popleft()
        
        # Remove numbers less than the current number
        while deck and nums[deck[-1]] < nums[i]:
            deck.pop()
        
        deck.append(i)
        
        if i >= k - 1:
            result.append(nums[deck[0]])
    
    return result