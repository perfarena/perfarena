from sortedcontainers import SortedList

def count_smaller_numbers(nums):
    sorted_nums = SortedList()
    counts = []

    for num in reversed(nums):
        index = sorted_nums.bisect_left(num)
        counts.append(len(sorted_nums) - index)
        sorted_nums.add(num)

    return counts[::-1]