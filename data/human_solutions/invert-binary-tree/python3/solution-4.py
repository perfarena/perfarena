def invert_tree(root):
    if root is None or not root:
        return
    root.left, root.right = root.right, root.left
    invert_tree(root.left)
    invert_tree(root.right)
    return root

# Call the function with the root of the binary tree
inverted_root = invert_tree(root)