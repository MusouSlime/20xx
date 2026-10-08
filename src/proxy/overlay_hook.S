/*
 * overlay_hook.S -- i386 trampoline replacing FUN_0045c220.
 *
 * FUN_0045c220 is __thiscall: this in ecx, one stack arg, returns with ret 4.
 * We push (offset, this) as cdecl args for inject_for_instance(), clean them,
 * then ret 4 to pop the original stack arg. Symbol names carry the i386 COFF
 * underscore prefix.
 */
	.text
	.globl	_overlay_hook
_overlay_hook:
	pushl	4(%esp)          /* offset */
	pushl	%ecx             /* this   */
	call	_inject_for_instance
	addl	$8, %esp
	ret	$4

/*
 * Inline-hook stub for FUN_0046c5c0 (the archive/loose asset reader).
 * Entry stack: [ret][arg1=name][arg2][arg3]. We log arg1, then run the
 * stolen prologue from the trampoline and jump back into the original.
 */
	.globl	_hook_asset
_hook_asset:
	pushal
	pushl	36(%esp)         /* arg1 (name) after pushal */
	call	_log_asset
	addl	$4, %esp
	popal
	jmp	*_g_tramp_asset

/*
 * Inline hook for TextureManagerDX11::UpdateTexture(tex, data).
 * Entry stack: [ret][tex][data]. Ask C whether to swap `data` for Mesen's
 * frame, then run the stolen prologue via the trampoline.
 */
	.globl	_mesen_tex_hook
_mesen_tex_hook:
	pushal
	/* After pushal (esp -= 32): [esp+36]=tex, [esp+40]=data. Push cdecl
	 * args right-to-left (data, then tex); the second push shifts esp by 4,
	 * so tex is at 40(%esp) of the *new* esp. */
	pushl	40(%esp)         /* data */
	pushl	40(%esp)         /* tex  */
	call	_mesen_tex_data
	addl	$8, %esp         /* esp back to the post-pushal value */
	movl	%eax, 44(%esp)   /* overwrite the data argument ([esp+44]) */
	popal
	jmp	*_g_tramp_tex
