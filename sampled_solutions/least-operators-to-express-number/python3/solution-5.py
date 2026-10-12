def longest_ones(nums, k):
    left = 0
    num_zeros = 0
    result = 0

    for right in range(len(nums)):
        if nums[right] == 0:
            num_zeros += 1

        while num_zeros > k:
            if nums[left] == 0:
                num_zeros -= 1
            left += 1

        result = max(result, right - left + 1)

    return result

# Test the function
nums = [1, 0, 1, 1, 0, 1]
k = 1
print(longest_ones(nums, k))  # Output: 4