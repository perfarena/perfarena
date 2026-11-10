def sumRootToLeaf(root, value=0):
    if root is None:
        return 0
    value = value * 10 + root.val
    if root.left is None and root.right is None:
        return value
    return sumRootToLeaf(root.left, value) + sumRootToLeaf(root.right, value)