public TreeNode invertTree(TreeNode root) {
    if (root == null) return root;
    
    TreeNode right = root.right;
    root.right = invertTree(root.left);
    root.left = invertTree(right);
    
    return root;
}