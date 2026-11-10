def invertTree(root):
    if not root:
        return None
    invertTree(root.left)
    invertTree(root.right)
    
    root.left, root.right = root.right, root.left
    
    return root