def searchInsert(nums, target):
    left, right = 0, len(nums) - 1
    
    while left <= right:
        midpoint = left + (right - left) // 2
        
        if nums[midpoint] == target:
            return midpoint
        elif nums[midpoint] < target:
            left = midpoint + 1
        else:
            right = midpoint - 1
    
    return left