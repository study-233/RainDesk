import * as Dialog from '@radix-ui/react-alert-dialog'
import {Slot} from '@radix-ui/react-slot'
import {cva} from 'class-variance-authority'
import type {ButtonHTMLAttributes, ReactNode} from 'react'
const button = cva('btn',{variants:{variant:{default:'primary',outline:'outline',ghost:'ghost'}},defaultVariants:{variant:'default'}})
export function Button({asChild,variant,className='',...props}:ButtonHTMLAttributes<HTMLButtonElement>&{asChild?:boolean;variant?:'default'|'outline'|'ghost'}) {
  const Tag=asChild?Slot:'button'
  return <Tag className={button({variant})+' '+className} {...props}/>
}
export function Confirm({label,title,children,action,variant='outline'}:{label:string;title:string;children:ReactNode;action:()=>void;variant?:'default'|'outline'|'ghost'}) {
  return <Dialog.Root><Dialog.Trigger asChild><Button variant={variant}>{label}</Button></Dialog.Trigger><Dialog.Portal><Dialog.Overlay className="overlay"/><Dialog.Content className="dialog"><Dialog.Title>{title}</Dialog.Title><Dialog.Description>{children}</Dialog.Description><footer><Dialog.Cancel asChild><Button variant="outline">取消</Button></Dialog.Cancel><Dialog.Action asChild><Button onClick={action}>确认</Button></Dialog.Action></footer></Dialog.Content></Dialog.Portal></Dialog.Root>
}
