import {ApiError} from './api.js';
// Selection is cleared synchronously before I/O. Only the latest request may
// publish details, so failed or out-of-order loads cannot redirect an import.
export class SessionSelection {
  constructor(fetchSession,onChange=()=>{}){this.fetchSession=fetchSession;this.onChange=onChange;this.revision=0;this.id='';this.session=null;this.pending=false;}
  publish(){this.onChange({id:this.id,session:this.session,pending:this.pending});}
  clear(){this.revision++;this.id='';this.session=null;this.pending=false;this.publish();}
  async select(id){
    const revision=++this.revision;this.id=id||'';this.session=null;this.pending=Boolean(id);this.publish();
    if(!id)return true;
    try{
      const record=await this.fetchSession(id);
      if(revision!==this.revision)return false;
      if(record?.id!==id)throw new ApiError({code:'session_mismatch',message:'Session readback did not match the selected ID. Import remains disabled.'});
      this.session=record;this.pending=false;this.publish();return true;
    }catch(error){if(revision!==this.revision)return false;this.session=null;this.pending=false;this.publish();throw error;}
  }
  forImport(visibleId){
    if(this.pending||!this.session||this.session.id!==this.id||visibleId!==this.id)
      throw new ApiError({code:'session_required',message:'Load the selected session successfully before importing. No file has been written.'});
    return this.session;
  }
}
