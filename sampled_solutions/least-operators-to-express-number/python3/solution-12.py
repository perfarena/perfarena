def findMaxConsecutiveOnes(nums, K):
    start = end = zeroCount = currentLength = maxLength = 0
    
    while end < len(nums):
        if nums[end] == 0:
            zeroCount += 1
        
        while zeroCount > K:
            if nums[start] == 0:
                zeroCount -= 1
            start += 1
        
        currentLength = end - start + 1
        maxLength = max(maxLength, currentLength)
        
        end += 1
    
    return maxLength