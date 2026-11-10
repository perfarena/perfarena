class TreeNode:
    def __init__(self, val):
        self.val = val
        self.left = None
        self.right = None
        self.count = 1

def countSmaller(nums):
    if not nums:
        return []

    root = TreeNode(nums[-1])
    result = [0]

    def insert(node, val, smaller):
        if val > node.val:
            smaller += node.count
            if node.right:
                insert(node.right, val, smaller)
            else:
                node.right = TreeNode(val)
        else:
            node.count += 1
            if node.left:
                insert(node.left, val, smaller)
            else:
                node.left = TreeNode(val)
                result[0] += smaller

    for i in range(len(nums) - 2, -1, -1):
        insert(root, nums[i], 0)
        result.insert(0, result[0])

    return result