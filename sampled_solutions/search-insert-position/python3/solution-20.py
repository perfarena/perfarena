def search_insert(nums, target):
    if target < nums[0]:
        return 0
    if target > nums[-1]:
        return len(nums)
    
    for i in range(len(nums)):
        if nums[i] == target:
            return i
        if nums[i] < target < nums[i+1]:
            return i + 1