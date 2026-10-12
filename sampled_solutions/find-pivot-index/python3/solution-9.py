def find_pivot_index(nums):
    total_sum = sum(nums)
    left_sum = 0
    right_sum = total_sum

    for i in range(len(nums)):
        right_sum -= nums[i]

        if left_sum == right_sum:
            return i

        left_sum += nums[i]

    return -1

# Test the function with examples
print(find_pivot_index([1, 7, 3, 6, 5, 6]))  # Output: 3
print(find_pivot_index([1, 2, 3]))  # Output: -1