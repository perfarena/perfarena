def first_missing_positive(nums):
    n = len(nums)
    
    for i in range(n):
        x = nums[i]
        nums[i] = 0
        
        while True:
            if x <= 0 or x > n or x == nums[x - 1]:
                break
            nums[x - 1], x = x, nums[x - 1]
    
    for i in range(n):
        if nums[i] == 0:
            return i + 1
    
    return n + 1