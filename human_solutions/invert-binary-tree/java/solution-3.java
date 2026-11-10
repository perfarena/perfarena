public TreeNode invertTree(TreeNode root) {
    if (root == null) {
        return null;
    }
    
    swap(root);
    
    invertTree(root.left);
    invertTree(root.right);
    
    return root;
}

private void swap(TreeNode node) {
    if (node == null) {
        return;
    }
    
    TreeNode temp = node.left;
    node.left = node.right;
    node.right = temp;
}