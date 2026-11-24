# Python code implementation
def firstMissingPositive(nums):
    # Check if 1 is present
    contains_one = False
    for num in nums:
        if num == 1:
            contains_one = True
            break
    
    # If 1 is missing, return 1
    if not contains_one:
        return 1
    
    # Transform the array
    n = len(nums)
    for i in range(n):
        if nums[i] <= 0 or nums[i] > n:
            nums[i] = 1
    
    # Mark the presence of numbers
    for i in range(n):
        x = abs(nums[i])
        if x == n:
            nums[0] = -abs(nums[0])
        else:
            nums[x] = -abs(nums[x])
    
    # Find the first missing positive number
    for i in range(1, n):
        if nums[i] > 0:
            return i
    
    # Handle the case where all numbers from 1 to n are present
    if nums[0] > 0:
        return n
    
    return n + 1