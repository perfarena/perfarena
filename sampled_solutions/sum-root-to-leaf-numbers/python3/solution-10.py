# Test cases
root = TreeNode(1)
root.left = TreeNode(0)
root.right = TreeNode(1)
print(sum_root_to_leaf(root))  # Output should be 11

root = TreeNode(4)
root.left = TreeNode(9)
root.right = TreeNode(0)
root.left.left = TreeNode(5)
root.left.right = TreeNode(1)
print(sum_root_to_leaf(root))  # Output should be 1026