def countSmallerToRight(nums):
    result = []
    for i in range(len(nums) - 1, -1, -1):
        count = 0
        for j in range(i + 1, len(nums)):
            if nums[j] < nums[i]:
                count += 1
        result.insert(0, count)
    return result

# Test the function
input_nums = [5, 2, 6, 1]
output = countSmallerToRight(input_nums)
print(output)  # Output should be [2, 1, 1, 0]