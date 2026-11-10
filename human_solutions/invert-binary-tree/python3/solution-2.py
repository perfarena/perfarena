def invertTree(root):
    if root is None:
        return None
    
    # Swap left and right children
    root.left, root.right = root.right, root.left
    
    # Recursively invert left and right subtrees
    invertTree(root.left)
    invertTree(root.right)
    
    return root