def find_pivot_index(nums):
    pivot = 0
    left_sum = 0
    right_sum = sum(nums) - nums[0]

    while pivot < len(nums):
        if left_sum == right_sum:
            return pivot
        else:
            pivot += 1
            if pivot < len(nums):
                left_sum += nums[pivot - 1]
                right_sum -= nums[pivot]

    return -1

# Test the function with your input array
nums = [1, 7, 3, 6, 5, 6]
result = find_pivot_index(nums)
print(result)