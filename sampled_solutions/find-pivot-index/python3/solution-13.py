def findPivotIndex(nums):
    totalSum = sum(nums)
    accumulateSum = 0
    
    for i in range(len(nums)):
        if accumulateSum == (totalSum - nums[i]) / 2:
            return i
        accumulateSum += nums[i]
    
    return -1