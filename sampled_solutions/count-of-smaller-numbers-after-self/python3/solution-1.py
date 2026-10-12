class Node:
    def __init__(self, value):
        self.value = value
        self.small_count = 0
        self.count = 0
        self.left = None
        self.right = None

def count_of_smaller_numbers(nums):
    def insert(node, number):
        if node.value > number:
            node.small_count += 1
            if not node.left:
                node.left = Node(number)
        else:
            node.count += 1
            if not node.right:
                node.right = Node(number)

    if not nums:
        return []

    root = Node(nums[-1])
    answer = [0] * (len(nums) - 1)

    for i in range(len(nums) - 2, -1, -1):
        node = root
        sum_count = 0

        while node.value != nums[i]:
            if node.value > nums[i]:
                if not node.left:
                    node.left = Node(nums[i])
                node.small_count += 1
                node = node.left
            else:
                sum_count += node.small_count + node.count
                if not node.right:
                    node.right = Node(nums[i])
                node = node.right

        node.count += 1
        answer[i] = sum_count + node.small_count

    return answer

# Sanity check
nums = [4, 6, 7, 8, 1, 2]
result = count_of_smaller_numbers(nums)
print(result)