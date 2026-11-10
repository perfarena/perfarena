def longest_positive_sequence(nums):
    max_length = 0
    num_set = set(nums)

    for num in nums:
        if num - 1 not in num_set:
            current_num = num
            current_length = 1

            while current_num + 1 in num_set:
                current_num += 1
                current_length += 1

            max_length = max(max_length, current_length)

    return max_length

# Test the function with a list of numbers
numbers = [100, 80, 101, 991, 992, 993, 100, 102, 103, 104, 105, 106]
result = longest_positive_sequence(numbers)
print("Length of the longest positive sequence:", result)