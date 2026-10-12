def find_pivot_index(nums):
    left_sum = [0] * len(nums)
    right_sum = [0] * len(nums)
    
    # Calculate sum of elements to the left of each index
    for i in range(len(nums)):
        left_sum[i] = left_sum[i - 1] + nums[i] if i > 0 else 0
    
    # Reverse the array and calculate sum of elements to the left of each index
    for i in range(len(nums) - 1, -1, -1):
        right_sum[i] = right_sum[i + 1] + nums[i] if i < len(nums) - 1 else 0
    
    # Find the pivot index
    for i in range(len(nums)):
        if left_sum[i] == right_sum[i]:
            return i
    
    return -1

# Test the function
nums = [1, 7, 3, 6, 5, 6]
pivot_index = find_pivot_index(nums)
print("Pivot Index:", pivot_index)